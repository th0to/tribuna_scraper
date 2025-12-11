from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from scrapy.http import HtmlResponse
from scrapy import signals
import json
import time

class SeleniumMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler
        self.settings = crawler.settings
        self.driver_name = self.settings.get("SELENIUM_DRIVER_NAME", "chrome")
        self.driver_executable_path = self.settings.get("SELENIUM_DRIVER_EXECUTABLE_PATH")
        self.driver_arguments = self.settings.get("SELENIUM_DRIVER_ARGUMENTS", [])
        self.driver = None

        crawler.signals.connect(self.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(self.spider_closed, signal=signals.spider_closed)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def spider_opened(self, spider):
        if self.driver_name.lower() == "chrome" or self.driver_name.lower() == "chromium":
            options = Options()
            for arg in self.driver_arguments:
                options.add_argument(arg)
            options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            service = Service(executable_path=self.driver_executable_path)
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.implicitly_wait(10) # Attente implicite de 10 secondes

    def spider_closed(self, spider):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def process_request(self, request, spider):
        if not request.meta.get("selenium", False):
            return None

        if not self.driver:
            self.spider_opened(spider)

        self.driver.get(request.url)

        # Sauvegarder une capture d\"écran après le chargement initial
        screenshot_path = "/home/ubuntu/tribuna_scraper/tribuna_initial_load_screenshot.png"
        self.driver.save_screenshot(screenshot_path)
        spider.logger.info(f"Screenshot saved to {screenshot_path}")

        # Sauvegarder le HTML immédiatement après le chargement pour le débogage
        with open("/home/ubuntu/tribuna_scraper/tribuna_initial_load_debug.html", "w", encoding="utf-8") as f:
            f.write(self.driver.page_source)
        spider.logger.info("HTML saved to /home/ubuntu/tribuna_scraper/tribuna_initial_load_debug.html")

        selenium_actions = request.meta.get("selenium_actions", [])
        for action in selenium_actions:
            action_type = action["action"]
            timeout = action.get("timeout", 10)

            if action_type == "wait_for_element":
                selector = action["selector"]
                by = getattr(By, action.get("by", "xpath").replace(" ", "_").upper())
                clickable = action.get("clickable", False)
                try:
                    if clickable:
                        WebDriverWait(self.driver, timeout).until(
                            EC.element_to_be_clickable((by, selector))
                        )
                    else:
                        WebDriverWait(self.driver, timeout).until(
                            EC.presence_of_element_located((by, selector))
                        )
                except Exception as e:
                    spider.logger.error(f"Timeout waiting for element {selector}: {e}")
                    raise

            elif action_type == "execute_script":
                script = action["script"]
                try:
                    self.driver.execute_script(script)
                except Exception as e:
                    spider.logger.error(f"Error executing script: {script} - {e}")
                    raise

            elif action_type == "save_html":
                path = action["path"]
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                spider.logger.info(f"HTML saved to {path}")

            elif action_type == "click":
                selector = action["selector"]
                by = getattr(By, action.get("by", "xpath").replace(" ", "_").upper())
                try:
                    WebDriverWait(self.driver, timeout).until(
                        EC.element_to_be_clickable((by, selector))
                    ).click()
                except Exception as e:
                    spider.logger.error(f"Error clicking element {selector}: {e}")
                    raise

            elif action_type == "send_keys":
                selector = action["selector"]
                by = getattr(By, action.get("by", "xpath").replace(" ", "_").upper())
                keys = action["keys"]
                try:
                    element = WebDriverWait(self.driver, timeout).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    element.send_keys(keys)
                except Exception as e:
                    spider.logger.error(f"Error sending keys to element {selector}: {e}")
                    raise

            elif action_type == "wait":
                time.sleep(action["time"])

            elif action_type == "save_screenshot":
                path = action["path"]
                self.driver.save_screenshot(path)
                spider.logger.info(f"Screenshot saved to {path}")

            elif action_type == "switch_to_frame":
                frame_selector = action["frame_selector"]
                by = getattr(By, action.get("by", "xpath").replace(" ", "_").upper())
                try:
                    WebDriverWait(self.driver, timeout).until(
                        EC.frame_to_be_available_and_switch_to_it((by, frame_selector))
                    )
                    spider.logger.info(f"Switched to frame: {frame_selector}")
                except Exception as e:
                    spider.logger.error(f"Error switching to frame {frame_selector}: {e}")
                    raise

            elif action_type == "switch_to_default_content":
                self.driver.switch_to.default_content()
                spider.logger.info("Switched to default content.")

        # Capture performance logs after all actions are done
        logs = self.driver.get_log("performance")
        gwt_rpc_body = None
        for entry in logs:
            log = json.loads(entry["message"])["message"]
            if log["method"] == "Network.requestWillBeSent" and "request" in log["params"]:
                request_url = log["params"]["request"]["url"]
                request_method = log["params"]["request"]["method"]
                if "loadTable" in request_url and request_method == "POST":
                    # Attempt to get postData from the requestWillBeSent event first
                    gwt_rpc_body = log["params"]["request"].get("postData")
                    if gwt_rpc_body:
                        spider.logger.info(f"Found GWT RPC request body in requestWillBeSent: {gwt_rpc_body}")
                        break
                    else:
                        # If postData is not directly available, try to get it using Network.getRequestPostData
                        request_id = log["params"]["requestId"]
                        try:
                            # This requires a CDP connection, which is not directly exposed by Selenium's Python bindings
                            # For now, we'll rely on postData being in requestWillBeSent. If not, manual inspection is needed.
                            spider.logger.warning(f"postData not found in requestWillBeSent for {request_url}. Cannot use Network.getRequestPostData directly via Selenium Python.")
                        except Exception as e:
                            spider.logger.error(f"Error getting postData for request {request_id}: {e}")

        body = self.driver.page_source
        response = HtmlResponse(self.driver.current_url, body=body, encoding="utf-8", request=request)
        if gwt_rpc_body:
            response.meta["gwt_rpc_body"] = gwt_rpc_body
        return response

