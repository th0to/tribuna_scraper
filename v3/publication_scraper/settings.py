# Scrapy settings for publication_scraper project
import os
from dotenv import load_dotenv
from pathlib import Path

# Charger les variables d'environnement
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

BOT_NAME = 'publication_scraper'

SPIDER_MODULES = ['publication_scraper.spiders']
NEWSPIDER_MODULE = 'publication_scraper.spiders'

# Respecter les robots.txt
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests
# Mode async comme le client, ajuster selon besoins
CONCURRENT_REQUESTS = int(os.getenv('CONCURRENT_REQUESTS', 1))

# Configure a delay for requests (seconds)
# Client utilise 0.75-1s, aligné sur 1s par défaut
DOWNLOAD_DELAY = float(os.getenv('DOWNLOAD_DELAY', 1))

# Disable cookies
COOKIES_ENABLED = False

# Override the default request headers
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    'User-Agent': os.getenv('USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES = {
    'scrapy.spidermiddlewares.httperror.HttpErrorMiddleware': 50,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
}

# Configure item pipelines
ITEM_PIPELINES = {
    'publication_scraper.pipelines.PublicationPipeline': 300,
}

# Enable and configure HTTP caching
HTTPCACHE_ENABLED = os.getenv('CACHE_ENABLED', 'false').lower() == 'true'

# Timeouts
DOWNLOAD_TIMEOUT = int(os.getenv('DOWNLOAD_TIMEOUT', 60))

# Logs
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# Retry settings (important pour stabilité en production)
RETRY_TIMES = int(os.getenv('RETRY_TIMES', 3))
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408, 429]

# AutoThrottle (optionnel, pour adaptation automatique du delay)
AUTOTHROTTLE_ENABLED = os.getenv('AUTOTHROTTLE_ENABLED', 'false').lower() == 'true'
AUTOTHROTTLE_START_DELAY = float(os.getenv('AUTOTHROTTLE_START_DELAY', 1))
AUTOTHROTTLE_MAX_DELAY = float(os.getenv('AUTOTHROTTLE_MAX_DELAY', 10))
AUTOTHROTTLE_TARGET_CONCURRENCY = float(os.getenv('AUTOTHROTTLE_TARGET_CONCURRENCY', 1.0))
