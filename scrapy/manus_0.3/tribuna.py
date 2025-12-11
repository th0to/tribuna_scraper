import scrapy
from scrapy.http import Request
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import asyncio
import json

class TribunaSpider(scrapy.Spider):
    name = "tribuna"
    allowed_domains = ["publicationtc.fr.ch"]
    start_urls = ["https://publicationtc.fr.ch/?locale=fr"]

    def start_requests(self):
        for url in self.start_urls:
            yield Request(
                url=url,
                callback=self.parse_search_page,
                meta={
                    "selenium": True,
                    "selenium_actions": [
                        # Attendre un délai plus long pour permettre au contenu GWT de se charger
                        {"action": "wait", "time": 15},
                        {"action": "save_html", "path": "/home/ubuntu/tribuna_scraper/tribuna_initial_load_debug.html", "timeout": 0},
                        {"action": "save_screenshot", "path": "/home/ubuntu/tribuna_scraper/tribuna_initial_load_debug.png", "timeout": 0},
                        
                        # Injecter du JavaScript pour intercepter les requêtes XMLHttpRequest et capturer le corps de la requête GWT RPC
                        {"action": "execute_script", "script": """
                            window.captured_request_body = null;
                            const original_send = XMLHttpRequest.prototype.send;
                            XMLHttpRequest.prototype.send = function(body) {
                                if (this._url && this._url.includes("loadTable")) {
                                    window.captured_request_body = body;
                                }
                                original_send.apply(this, arguments);
                            };
                            const original_open = XMLHttpRequest.prototype.open;
                            XMLHttpRequest.prototype.open = function(method, url) {
                                this._url = url;
                                original_open.apply(this, arguments);
                            };
                        """},
                        
                        # Attendre que le champ de recherche soit visible et interactif
                        {"action": "wait_for_element", "selector": "input.gwt-TextBox[placeholder=\"Plein texte\"]", "by": "css selector", "timeout": 60, "clickable": True},
                        # Entrer le texte dans le champ de recherche
                        {"action": "send_keys", "selector": "input.gwt-TextBox[placeholder=\"Plein texte\"]", "by": "css selector", "keys": "test", "timeout": 30},
                        
                        # Attendre que le bouton de recherche soit visible et cliquable
                        {"action": "wait_for_element", "selector": "div.button-icon-search", "by": "css selector", "timeout": 60, "clickable": True},
                        # Cliquer sur le bouton de recherche
                        {"action": "click", "selector": "div.button-icon-search", "by": "css selector", "timeout": 30},
                        
                        {"action": "save_html", "path": "/home/ubuntu/tribuna_scraper/tribuna_button_clicked.html", "timeout": 0},
                        {"action": "save_screenshot", "path": "/home/ubuntu/tribuna_scraper/tribuna_button_clicked.png", "timeout": 0},
                        
                        # Attendre que les résultats de recherche apparaissent
                        {"action": "wait_for_element", "selector": "div.list-item-publ", "by": "css selector", "timeout": 90, "clickable": False},
                        {"action": "save_html", "path": "/home/ubuntu/tribuna_scraper/tribuna_results_loaded.html", "timeout": 0},
                        {"action": "save_screenshot", "path": "/home/ubuntu/tribuna_scraper/tribuna_results_loaded.png", "timeout": 0},

                        # Récupérer le corps de la requête GWT RPC capturé par le JavaScript injecté
                        {"action": "execute_script", "script": "return window.captured_request_body;", "return_value": True, "key": "gwt_rpc_body"},
                    ]
                }
            )

    def parse_search_page(self, response):
        self.logger.info("Search performed. Extracting results.")
        gwt_rpc_body = response.meta.get("gwt_rpc_body")
        if gwt_rpc_body:
            self.logger.info(f"GWT RPC Body captured: {gwt_rpc_body}")
            with open("/home/ubuntu/tribuna_scraper/gwt_rpc_body.txt", "w") as f:
                f.write(gwt_rpc_body)
        else:
            self.logger.warning("Could not capture GWT RPC body.")

        results = response.css("div.list-item-publ")
        self.logger.info(f"Found {len(results)} results on page {response.url}")
        for result in results:
            yield {
                "title": result.css("div.list-item-content-publ-title::text").get(),
                "summary": result.css("div.list-item-content-publ-summary::text").get(),
            }

        next_page_button = response.css("div.page-button-next-publ")
        if next_page_button:
            if "page-button-next-publ-disabled" not in next_page_button.attrib["class"]:
                yield Request(
                    url=response.url,
                    callback=self.parse_search_results,
                    meta={
                        "selenium": True,
                        "selenium_actions": [
                            {"action": "click", "selector": "div.page-button-next-publ", "by": "css selector", "timeout": 10},
                            {"action": "wait_for_element", "selector": "div.list-item-publ", "by": "css selector", "timeout": 20, "clickable": False}
                        ]
                    },
                    dont_filter=True
                )

    def parse_search_results(self, response):
        # This method will be called for subsequent pages if pagination is implemented
        self.logger.info("Parsing search results page.")
        gwt_rpc_body = response.meta.get("gwt_rpc_body")
        if gwt_rpc_body:
            self.logger.info(f"GWT RPC Body captured on results page: {gwt_rpc_body}")

        results = response.css("div.list-item-publ")
        self.logger.info(f"Found {len(results)} results on page {response.url}")
        for result in results:
            yield {
                "title": result.css("div.list-item-content-publ-title::text").get(),
                "summary": result.css("div.list-item-content-publ-summary::text").get(),
            }

        next_page_button = response.css("div.page-button-next-publ")
        if next_page_button:
            if "page-button-next-publ-disabled" not in next_page_button.attrib["class"]:
                yield Request(
                    url=response.url,
                    callback=self.parse_search_results,
                    meta={
                        "selenium": True,
                        "selenium_actions": [
                            {"action": "click", "selector": "div.page-button-next-publ", "by": "css selector", "timeout": 10},
                            {"action": "wait_for_element", "selector": "div.list-item-publ", "by": "css selector", "timeout": 20, "clickable": False}
                        ]
                    },
                    dont_filter=True
                )

