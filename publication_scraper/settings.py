"""Scrapy settings for publication_scraper project.

Règle du projet (demandée): ne pas dépendre de variables d'environnement ou de .env.

Tous les réglages restent overrideables via Scrapy:
- CLI: `-s KEY=VALUE`
- ou via un autre settings module si besoin.
"""

BOT_NAME = 'publication_scraper'

SPIDER_MODULES = ['publication_scraper.spiders']
NEWSPIDER_MODULE = 'publication_scraper.spiders'

# Respecter les robots.txt
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests
# Mode async comme le client, ajuster selon besoins
CONCURRENT_REQUESTS = 1

# Configure a delay for requests (seconds)
# Client utilise 0.75-1s, aligné sur 1s par défaut
DOWNLOAD_DELAY = 1.0

# Disable cookies
COOKIES_ENABLED = False

# Override the default request headers
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES = {
    'scrapy.spidermiddlewares.httperror.HttpErrorMiddleware': 50,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'publication_scraper.middlewares.PDFDownloadMiddleware': 610,
}

# Files storage (used by downloader middleware)
# - Override possible via `-s FILES_STORE=...`
FILES_STORE = './output/pdfs'
MEDIA_ALLOW_REDIRECTS = True

# Configure item pipelines (only metadata; download is handled in middleware)
ITEM_PIPELINES = {
    'publication_scraper.pipelines.PublicationPipeline': 300,
}

# Enable and configure HTTP caching
HTTPCACHE_ENABLED = False

# Timeouts
DOWNLOAD_TIMEOUT = 60

# Logs
LOG_LEVEL = 'INFO'
LOG_SHORT_NAMES = True

# Retry settings (important pour stabilité en production)
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408, 429]

# AutoThrottle (optionnel, pour adaptation automatique du delay)
AUTOTHROTTLE_ENABLED = False
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# Activer le réacteur asyncio de Twisted pour support des callbacks async (Scrapy >= 2.13)
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'

# Fingerprinter: Scrapy 2.13+ utilise le fingerprinter moderne par défaut.
# On évite de forcer REQUEST_FINGERPRINTER_IMPLEMENTATION (déprécié).

# Dupefilter: compatible avec fingerprinter 2.7 (valeur par défaut Scrapy)
DUPEFILTER_CLASS = 'scrapy.dupefilters.RFPDupeFilter'

# Mode strict par défaut (validation de complétude).
# Override possible via: `-s STRICT_FULL=0`
STRICT_FULL = True
