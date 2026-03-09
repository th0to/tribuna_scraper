"""GWT Raw Page Saver — Scrapy Extension.

Saves raw GWT //OK[...] responses to disk for offline analysis and validation.

Activation (add to settings.py or via CLI):
  EXTENSIONS = {
      'publication_scraper.lib.gwt_saver.GwtPageSaverExtension': 500,
  }
  GWT_SAVE_DIR = 'saved_pages/fribourg'   # directory to write pages into
  GWT_SAVE_ENABLED = True                  # can disable without removing from settings

Or activate temporarily via CLI:
  scrapy crawl fribourg -s GWT_SAVE_ENABLED=1 -s GWT_SAVE_DIR=saved_pages/fribourg

Output files:
  saved_pages/fribourg/page_00.txt   ← raw //OK[...] responses, one per page
  saved_pages/fribourg/page_01.txt
  ...

Usage for offline extraction:
  python gwt_multipage_extract.py saved_pages/fribourg/page_*.txt
"""

import logging
import os
from pathlib import Path
from scrapy import signals
from scrapy.extensions import Extension

logger = logging.getLogger(__name__)


class GwtPageSaverExtension:
    """Scrapy extension that intercepts GWT responses and saves them to disk."""

    def __init__(self, save_dir: str, enabled: bool):
        self.save_dir = Path(save_dir)
        self.enabled  = enabled
        self._page_counter = 0

    @classmethod
    def from_crawler(cls, crawler):
        enabled  = crawler.settings.getbool('GWT_SAVE_ENABLED', False)
        save_dir = crawler.settings.get('GWT_SAVE_DIR', 'saved_pages/gwt')
        ext = cls(save_dir=save_dir, enabled=enabled)
        crawler.signals.connect(ext.response_received, signal=signals.response_received)
        return ext

    def response_received(self, response, request, spider):
        if not self.enabled:
            return
        body = response.text if hasattr(response, 'text') else ''
        if not body.startswith('//OK'):
            return   # Not a GWT response

        if self._page_counter == 0:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"GwtPageSaver: saving pages to {self.save_dir}")

        filename = self.save_dir / f'page_{self._page_counter:02d}.txt'
        filename.write_text(body, encoding='utf-8')
        logger.info(f"GwtPageSaver: saved page {self._page_counter} → {filename}")
        self._page_counter += 1
