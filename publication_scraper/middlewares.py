import os
import re
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from scrapy import signals
from itemadapter import ItemAdapter


class PDFDownloadMiddleware:
    """Downloader middleware that saves PDF responses to FILES_STORE.

    It triggers when the response looks like a PDF (content-type contains 'pdf' or URL ends with .pdf).
    Filenames are derived from DocId > Num > URL basename and organized under full/<Kanton>/.
    """

    def __init__(self, files_store: str):
        self.files_store = Path(files_store).resolve()
        self.files_store.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_crawler(cls, crawler):
        store = crawler.settings.get('FILES_STORE', './output/pdfs')
        mw = cls(store)
        crawler.signals.connect(mw.spider_opened, signal=signals.spider_opened)
        return mw

    def spider_opened(self, spider):
        spider.logger.info(f"PDFDownloadMiddleware actif, stockage: {self.files_store}")

    def process_response(self, request, response, spider):
        status = getattr(response, 'status', None)
        if status not in (200, 206):
            return response
        # Detect PDF by header or URL suffix
        ct = response.headers.get('Content-Type') or response.headers.get(b'Content-Type')
        if isinstance(ct, bytes):
            ct = ct.decode('utf-8', errors='ignore')
        looks_pdf = False
        if ct and 'pdf' in ct.lower():
            looks_pdf = True
        if not looks_pdf and str(response.url).lower().endswith('.pdf'):
            looks_pdf = True

        if not looks_pdf:
            return response

        adapter = None
        item = request.meta.get('item') if request else None
        if item is not None:
            adapter = ItemAdapter(item)

        docid = adapter.get('DocId') if adapter else None
        num = adapter.get('Num') if adapter else None
        canton = adapter.get('Kanton') if adapter else None

        parsed = urlparse(response.url)
        url_name = os.path.basename(parsed.path) or 'document'
        name_root, name_ext = os.path.splitext(url_name)
        # Some endpoints embed titles with dots (e.g. "art._38_...") in the URL, which makes
        # splitext think the suffix is an extension. We always store as a real PDF.
        if not name_ext or name_ext.lower() != '.pdf':
            name_ext = '.pdf'
        base = docid or num or name_root or 'document'
        base = re.sub(r'[^A-Za-z0-9._-]+', '_', str(base)).strip('._-')
        if not base:
            base = 'document'

        # Windows has practical path length limits; some endpoints embed long titles in the URL.
        # Keep filenames deterministic but bounded.
        max_base_len = 120
        if len(base) > max_base_len:
            digest = hashlib.sha1(base.encode('utf-8', errors='ignore')).hexdigest()[:16]
            base = f"{base[:80]}_{digest}"

        # Build destination path
        parts = [self.files_store, 'full']
        if canton:
            parts.append(canton)
        dest_dir = Path(*parts)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{base}{name_ext}"

        try:
            with open(dest_path, 'wb') as fh:
                fh.write(response.body)
            spider.logger.info(f"PDF sauvegardé: {dest_path}")
        except Exception as exc:
            spider.logger.error(f"Échec sauvegarde PDF {response.url}: {exc}")

        return response
