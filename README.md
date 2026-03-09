# Tribuna Scraper

Swiss tribunal publications scraper using Scrapy 2.13+ and GWT-RPC endpoints.

**📚 Full documentation**: See [readme/README.md](readme/README.md)

## Quick Start

```powershell
# Setup (installs Scrapy 2.13+, NO Playwright)
./scripts/setup.ps1

# Run spider (Fribourg, incremental last 30 days)
scrapy crawl fribourg -a days=30

# Run any canton
scrapy crawl tribuna -a canton=schwyz -a days=7

# Tests
pytest tests/
```

## Key Docs

- [readme/ALGORITHME_ST_BASED.md](readme/ALGORITHME_ST_BASED.md) - **Current date extraction algorithm** (98.1% PDatum accuracy)
- [readme/README_DATES.md](readme/README_DATES.md) - Date detection overview (EDatum + PDatum)
- [readme/PDATUM_SLIDING_LIMITATION.md](readme/PDATUM_SLIDING_LIMITATION.md) - Known residual limitation (~1%)
- [readme/README.md](readme/README.md) - Installation and usage guide
- [readme/README_LOGIQUE.md](readme/README_LOGIQUE.md) - Technical architecture (GWT-RPC deep-dive)

## Tech Stack

- **Python 3.12+**
- **Scrapy 2.13+** (pure HTTP scraping, NO browser automation)
- GWT-RPC parsing (string table extraction)
- Async/await flow with barrier synchronization

**⚠️ Important**: This scraper uses pure HTTP requests to GWT-RPC endpoints. NO Playwright, Selenium, or browser automation is used or needed.
