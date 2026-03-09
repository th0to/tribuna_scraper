"""Fribourg Spider - Spider Scrapy pour les publications du Tribunal Fribourg.

Spider autonome pour Fribourg avec la logique complète de détection de dates.

ARCHITECTURE DU LIEN DocId ↔ PDatum ↔ PDF:
==========================================
1. GWT-RPC retourne une réponse tokenisée contenant DocIds, dates, et chemins chiffrés
2. Pour chaque DocId, on détecte PDatum avec plusieurs stratégies:
   - D'abord depuis les tokens (stratégie last_in_row)
   - Puis fallback: scan du contenu brut autour du DocId
   - Propagation d'un "hint" entre items pour cohérence
3. Le chemin PDF (chiffré) est détecté et envoyé en POST pour décryptage
4. Les URLs candidates sont testées pour trouver le bon PDF

Usage:
    # Scraping complet
    scrapy crawl fribourg

    # Scraping incrémental (15 derniers jours)
    scrapy crawl fribourg -a days=15

    # Limite de pages
    scrapy crawl fribourg -s MAX_PAGES=3
"""

import re
import json
import logging
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta, date, timezone
from typing import List, Dict, Optional, Tuple, Any, AsyncIterator, Set
from urllib.parse import urljoin

import scrapy
from scrapy.http import Response, Request

from publication_scraper.items import PublicationItem
from publication_scraper.spiders import gwt_utils

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTES & PATTERNS
# ============================================================================

# DocId: 32 hex chars ou 15-17 digits
RE_ID = re.compile(r'[0-9a-f]{32}|[0-9]{15,17}')

# Dates
RE_DATE_ISO = re.compile(r'\d{4}-\d{2}-\d{2}')
RE_DATE_CH = re.compile(r'\d{2}\.\d{2}\.\d{4}')
RE_DATE_SLASH = re.compile(r'\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b')
RE_MONTH_NAME = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})\b", re.IGNORECASE)

# Mapping mois
MONTH_MAP = {
    'janvier': 1, 'janv': 1, 'jan': 1, 'january': 1,
    'février': 2, 'fevrier': 2, 'fevr': 2, 'february': 2, 'feb': 2,
    'mars': 3, 'mar': 3, 'march': 3,
    'avril': 4, 'avr': 4, 'april': 4, 'apr': 4,
    'mai': 5, 'may': 5,
    'juin': 6, 'jun': 6, 'june': 6,
    'juillet': 7, 'jul': 7, 'july': 7,
    'août': 8, 'aout': 8, 'aug': 8, 'august': 8,
    'septembre': 9, 'sept': 9, 'september': 9,
    'octobre': 10, 'oct': 10, 'october': 10,
    'novembre': 11, 'nov': 11, 'november': 11,
    'décembre': 12, 'decembre': 12, 'dec': 12, 'december': 12,
}

# PDF paths
RE_PFAD = re.compile(r'[A-Z]:\\[^"]+\.pdf', re.IGNORECASE)
RE_PFAD2 = re.compile(r'[0-9a-fA-F]{64,}')

# Numéros de dossier
NUM_PATTERNS = [
    re.compile(r'^\d{1,4}\s+\d{4}\s+\d{1,6}$'),
    re.compile(r'^\d{1,4}_\d{4}_\d{1,6}$'),
    re.compile(r'[A-Z]{1,5}\s?\d{2,4}\s?\d{1,4}'),
]

# Fenêtre de tokens
ROW_WINDOW_BEFORE = 25
ROW_WINDOW_AFTER = 60
# NOTE: MAX_IDS_PER_PAGE doit être >= nombre de DocIds uniques par page.
# Avec page_size=20 → ~15 DocIds uniques. Avec page_size=100 → ~75-100 DocIds uniques.
# Valeur 200 couvre confortablement les deux cas.
MAX_IDS_PER_PAGE = 200
NUM_SEARCH_RADIUS = 8
MAX_JOINED_TOKENS = 4

# Minimum response length pour considérer une page valide
MINIMUM_PAGE_LEN = 100

# Limites pour le calcul du marker (pdatum_hint)
# Aligné sur MAX_IDS_PER_PAGE pour couvrir toute la page
MAX_IDS_FOR_HINT_COMPUTATION = 200  # Nombre max de DocIds à traiter pour le hint
MAX_FUTURE_DAYS_FALLBACK = 400     # Cutoff dates futures en jours (> 1 an)


class FribourgSpider(scrapy.Spider):
    """Spider pour extraire les publications du Tribunal de Fribourg.
    
    Logique clé: lien DocId ↔ PDatum ↔ PDF
    - PDatum est détecté via tokens + fallback contenu brut
    - PDF path détecté → décrypté → URLs candidates testées
    """
    
    name = 'fribourg'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 1.0,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'AUTOTHROTTLE_ENABLED': True,
        'COOKIES_ENABLED': True,
    }
    
    # Limites de bootstrap retry
    MAX_BOOTSTRAP_RETRIES = 3
    
    def __init__(self, days=None, *args, **kwargs):
        """
        Args:
            days: Nombre de jours pour le mode incrémental (optionnel)
        """
        super().__init__(*args, **kwargs)
        
        # Charger la configuration Fribourg
        config_path = Path(__file__).parent.parent / 'cantons_config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        
        if 'fribourg' not in configs:
            raise ValueError("Configuration 'fribourg' manquante dans cantons_config.json")
            
        self.config = configs['fribourg']
        self.kanton_kurz = 'FR'
        self.canton = 'fribourg'
        
        # Mode incrémental
        self.days = int(days) if days is not None else None
        self.today = datetime.now(timezone.utc).date()
        self.min_date: Optional[date] = None
        self.max_date: Optional[date] = None
        
        if self.days and self.days > 0:
            self.min_date = self.today - timedelta(days=self.days)
            self.max_date = self.today
            logger.info(f"Mode incrémental: {self.min_date} -> {self.max_date}")
        else:
            logger.info("Mode complet: scraping de toutes les publications")
        
        # Cutoffs pour dates futures
        self.pdatum_future_days = int(self.config.get('pdatum_future_days', 0))
        self.edatum_future_days = int(self.config.get('edatum_future_days', 14))
        self.future_cutoff_pd = self.today + timedelta(days=self.pdatum_future_days)
        self.future_cutoff_ed = self.today + timedelta(days=self.edatum_future_days)
        
        # URLs
        self.result_page_url = self.config['result_page_url']
        self.decrypt_page_url = self.config['decrypt_page_url']
        self.download_url = self.config.get('download_url', self.config.get('base_url', ''))
        self.headers = dict(self.config['headers'])
        
        # Templates GWT
        self.result_query_tpl = self.config['result_query_tpl']
        self.result_query_sort = self.config.get('ui_publication_sort_body', self.result_query_tpl)
        
        # Decrypt templates
        self.DECRYPT_START = self.config.get('decrypt_start', '')
        self.DECRYPT_END = self.config.get('decrypt_end', '')
        self.ENCRYPTED = self.config.get('encrypted', True)
        self.ASCII_ENCRYPTED = self.config.get('ascii_encrypted', False)
        
        # État pagination
        self.page_nr = 0
        self.trefferzahl = 0
        self.pages_processed = 0
        self.items_emitted = 0
        self.seen_ids: set = set()
        self._yielded_docids: set = set()
        self._older_seen = False
        self._bootstrap_retry_count = 0
        self._bootstrap_perm_found = False
        
        # Barrier pour synchroniser pagination et PDF
        self.PAGE_PDF_BARRIER = self.config.get('page_pdf_barrier', True)
        self._page_pending_docids: Dict[int, set] = {}
        self._page_next_request: Dict[int, Optional[Request]] = {}
        
        # PDatum hint propagation (Fribourg-specific)
        self._pdatum_hint_by_docid: Dict[str, str] = {}
        
        # Limites
        self.MAX_PAGES = int(self.config.get('max_pages', 0) or 0)
        self.MAX_ITEMS = 0
        
        # Domaines autorisés
        domain = self.config['base_url'].replace('https://', '').replace('http://', '').rstrip('/')
        self.allowed_domains = [domain]
        
        logger.info(f"Spider Fribourg initialisé (barrier={self.PAGE_PDF_BARRIER})")
    
    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        """Récupère les settings depuis le crawler."""
        spider = super().from_crawler(crawler, *args, **kwargs)
        try:
            max_pages = crawler.settings.getint('MAX_PAGES', 0)
            if max_pages:
                spider.MAX_PAGES = max_pages
        except Exception:
            pass
        try:
            max_items = crawler.settings.getint('MAX_ITEMS', 0)
            if max_items:
                spider.MAX_ITEMS = max_items
        except Exception:
            pass
        return spider
    
    # =========================================================================
    # START REQUESTS & BOOTSTRAP
    # =========================================================================
    
    async def start_requests(self):  # type: ignore[override]
        """Standard Scrapy entrypoint (async, Scrapy 2.13+)."""
        async for req in self.start():
            yield req
    
    async def start(self) -> AsyncIterator[Request]:
        """Point d'entrée: lance le bootstrap GWT."""
        bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url', self.result_page_url)
        
        logger.info(f"Run config: canton={self.canton} days={self.days} min_date={self.min_date} max_date={self.max_date}")
        
        yield scrapy.Request(
            url=bootstrap_url,
            method="GET",
            headers=self.headers,
            callback=self.parse_bootstrap,
            errback=self.errback_general,
            meta={'referrer_policy': 'no-referrer', 'bootstrap': True},
            dont_filter=True
        )
    
    async def parse_bootstrap(self, response: Response):
        """Parse la page bootstrap pour extraire les tokens GWT."""
        logger.info(f"Bootstrap: {response.url} (status={response.status})")
        
        nocache_urls = self._update_gwt_tokens(response)
        
        # Si permutation non trouvée et nocache disponible, suivre le nocache.js
        if nocache_urls and not self._bootstrap_perm_found:
            logger.info(f"Suivi du nocache.js: {nocache_urls[0]}")
            yield scrapy.Request(
                url=nocache_urls[0],
                method="GET",
                headers=self.headers,
                callback=self.parse_nocache,
                errback=self.errback_general,
                meta={'referrer_policy': 'no-referrer'},
                dont_filter=True
            )
            return
        
        # Lancer la première requête POST
        for req in self._issue_first_post():
            yield req
    
    async def parse_nocache(self, response: Response):
        """Parse le nocache.js pour extraire le strongName."""
        text_nc = response.text or ''
        found_perm = None
        
        # Chercher strongName
        strong = re.search(r'strongName\s*[:=]\s*[\"\']([A-Za-z0-9._-]{8,})[\"\']', text_nc)
        if strong:
            found_perm = strong.group(1)
        
        # Fallback: *.cache.js
        if not found_perm:
            cache_hits = re.findall(r'([A-Za-z0-9._/-]+)\.cache\.js', text_nc)
            if cache_hits:
                found_perm = cache_hits[0].split('/')[-1].split('.')[0]
        
        # Fallback: hex32
        if not found_perm:
            hex32 = re.findall(r'"([A-Fa-f0-9]{32})"', text_nc) or re.findall(r"'([A-Fa-f0-9]{32})'", text_nc)
            if hex32:
                found_perm = hex32[0]
        
        if found_perm:
            self.headers['X-GWT-Permutation'] = found_perm
            logger.info(f"X-GWT-Permutation: {found_perm}")
        
        # Module-base fallback
        if 'X-GWT-Module-Base' not in self.headers:
            self.headers['X-GWT-Module-Base'] = str(response.url).rsplit('/', 1)[0] + '/'
        
        for req in self._issue_first_post():
            yield req
    
    def _update_gwt_tokens(self, response: Response) -> List[str]:
        """Met à jour les headers GWT depuis le HTML bootstrap."""
        html = response.text or ""
        base_url = str(response.url)
        
        found_perm, found_module, nocache_urls = gwt_utils.extract_bootstrap_tokens(
            html, base_url=base_url, response=response
        )
        
        if found_perm:
            self.headers['X-GWT-Permutation'] = found_perm
            logger.info(f"X-GWT-Permutation: {found_perm}")
        
        self._bootstrap_perm_found = bool(found_perm)
        
        if found_module:
            self.headers['X-GWT-Module-Base'] = found_module
            logger.info(f"X-GWT-Module-Base: {found_module}")
        
        return nocache_urls
    
    def _issue_first_post(self):
        """Émet la première requête POST loadTable."""
        # Si mode days avec sort et {trefferzahl} dans template, faire warmup
        if self.days and '{trefferzahl}' in self.result_query_sort:
            body = self._build_request_body(page_nr=0, use_sort=False)
            yield scrapy.Request(
                url=self.result_page_url,
                method="POST",
                body=body,
                headers=self.headers,
                callback=self.parse_page,
                errback=self.errback_general,
                meta={'page_nr': 0, 'warmup': True},
                dont_filter=True
            )
            return
        
        body = self._build_request_body(page_nr=0, use_sort=bool(self.days))
        self.page_nr = 1
        
        yield scrapy.Request(
            url=self.result_page_url,
            method="POST",
            body=body,
            headers=self.headers,
            callback=self.parse_page,
            errback=self.errback_general,
            meta={'page_nr': 0},
            dont_filter=True
        )
    
    def _build_request_body(self, page_nr: int, use_sort: bool = False) -> str:
        """Construit le body de la requête POST."""
        millis = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        
        if use_sort and self.trefferzahl > 0:
            tpl = self.result_query_sort
            body = tpl.replace('{page_nr}', str(page_nr))
            body = body.replace('{trefferzahl}', str(self.trefferzahl))
            body = body.replace('{millis}', millis)
        else:
            body = self.result_query_tpl.replace('{page_nr}', str(page_nr))
            body = body.replace('{millis}', millis)
        
        return body
    
    # =========================================================================
    # DATE PARSING - LOGIQUE COMPLÈTE FRIBOURG
    # =========================================================================
    
    def _parse_date_any(self, value: Any) -> Optional[date]:
        """Parse une valeur quelconque en date."""
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        
        s = str(value).strip()
        if not s:
            return None
        
        # ISO: YYYY-MM-DD
        if RE_DATE_ISO.fullmatch(s):
            try:
                return date.fromisoformat(s)
            except ValueError:
                pass
        
        # CH: DD.MM.YYYY
        if RE_DATE_CH.fullmatch(s):
            try:
                parts = s.split('.')
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except (ValueError, IndexError):
                pass
        
        # Slash: DD/MM/YYYY
        m = RE_DATE_SLASH.fullmatch(s)
        if m:
            try:
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return date(year, month, day)
            except ValueError:
                pass
        
        # Mois nommé: "15 janvier 2024"
        normalized = self._normalize_text(s)
        m = RE_MONTH_NAME.search(normalized)
        if m:
            try:
                day = int(m.group(1))
                month_name = m.group(2).lower().strip('.')
                year = int(m.group(3))
                month = MONTH_MAP.get(month_name)
                if month and 1 <= day <= 31:
                    return date(year, month, day)
            except ValueError:
                pass
        
        return None
    
    def _normalize_text(self, text: str) -> str:
        """Normalise le texte (NFKC)."""
        try:
            return unicodedata.normalize('NFKC', text or '')
        except Exception:
            return text or ''
    
    def _decode_context_text(self, raw: str) -> str:
        """Décode les séquences d'échappement dans le texte GWT."""
        if not raw:
            return ''
        try:
            result = raw.encode('utf-8', errors='ignore').decode('unicode_escape', errors='ignore')
        except Exception:
            result = raw
        return result
    
    def _scan_context_for_dates(self, text: str) -> List[str]:
        """Scan un texte pour trouver toutes les dates au format ISO."""
        results = []
        if not text:
            return results
        
        # ISO dates
        for m in RE_DATE_ISO.finditer(text):
            d = self._parse_date_any(m.group())
            if d:
                results.append(d.isoformat())
        
        # CH dates
        for m in RE_DATE_CH.finditer(text):
            d = self._parse_date_any(m.group())
            if d:
                results.append(d.isoformat())
        
        return results
    
    def _compute_pdatum_hint_by_docid_for_page(
        self,
        *,
        content: str,
        werte: List[str],
        id_indexes: List[int],
        docid_positions: Dict[str, List[int]],
    ) -> Dict[str, str]:
        """Calcule le pdatum_hint pour chaque DocId de la page.
        
        Mécanisme de propagation "marker":
        - On parcourt les DocIds dans l'ordre de leur position dans le contenu
        - Pour chaque DocId, on extrait le chunk jusqu'au prochain DocId
        - Si on trouve 2+ dates distinctes dans le chunk, la date MAX devient le "marker"
        - Ce marker se propage aux DocIds suivants
        
        Cela permet de propager la "date de publication" (souvent en en-tête de section)
        aux items suivants qui n'ont pas de date explicite.
        """
        pdatum_hint_by_docid: Dict[str, str] = {}
        
        if not content or not id_indexes:
            self._pdatum_hint_by_docid = {}
            return pdatum_hint_by_docid
        
        try:
            today = datetime.now(timezone.utc).date()
        except Exception:
            today = None
        
        # Limite de date future pour éviter les dates aberrantes
        future_cutoff = None
        if today:
            future_cutoff = today + timedelta(days=MAX_FUTURE_DAYS_FALLBACK)
        if self.max_date and (future_cutoff is None or self.max_date < future_cutoff):
            future_cutoff = self.max_date
        
        # Construire la liste des ancres (position, doc_id) triées par position
        anchors: List[Tuple[int, str]] = []
        seen: Set[str] = set()
        limit_ids = min(len(id_indexes), MAX_IDS_FOR_HINT_COMPUTATION)
        
        for idx in id_indexes[:limit_ids]:
            doc_id = self._safe_get(werte, idx, '')
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            pos_list = docid_positions.get(doc_id)
            if not pos_list:
                continue
            # Préférer les DocIds avec une seule occurrence (moins ambigus)
            if len(pos_list) == 1:
                anchors.append((int(pos_list[0]), doc_id))
        
        # Si pas d'ancres avec occurrence unique, prendre la première occurrence
        if not anchors:
            seen.clear()
            for idx in id_indexes[:limit_ids]:
                doc_id = self._safe_get(werte, idx, '')
                if not doc_id or doc_id in seen:
                    continue
                seen.add(doc_id)
                pos_list = docid_positions.get(doc_id)
                if pos_list:
                    anchors.append((int(pos_list[0]), doc_id))
        
        anchors.sort(key=lambda t: t[0])
        
        if not anchors:
            self._pdatum_hint_by_docid = {}
            return pdatum_hint_by_docid
        
        # Propager le marker
        current_marker: Optional[date] = None
        
        for i, (pos0, doc_id) in enumerate(anchors):
            # Fin du chunk = début du prochain DocId, ou fin de contenu
            pos1 = anchors[i + 1][0] if i + 1 < len(anchors) else None
            end = int(pos1) if isinstance(pos1, int) and pos1 > pos0 else min(len(content), pos0 + 60000)
            start = int(pos0)
            
            try:
                chunk = content[start:end]
            except Exception:
                chunk = ''
            
            chunk = self._decode_context_text(chunk)
            iso_list = self._scan_context_for_dates(chunk)
            
            # Dédupliquer tout en préservant l'ordre
            uniq_iso: List[str] = []
            for iso in iso_list:
                if iso not in uniq_iso:
                    uniq_iso.append(iso)
            
            # Convertir en dates valides
            distinct_dates: List[date] = []
            for iso in uniq_iso:
                d = self._parse_date_any(iso)
                if d is None:
                    continue
                if future_cutoff is not None and d > future_cutoff:
                    continue
                distinct_dates.append(d)
            
            # Si 2+ dates distinctes, le max devient le marker
            if len({d.isoformat() for d in distinct_dates}) >= 2:
                try:
                    marker = max(distinct_dates)
                except Exception:
                    marker = None
                
                if marker is not None:
                    # En mode desc (plus récents en premier), le marker ne peut pas augmenter
                    if current_marker is not None and marker > current_marker:
                        marker = None
                    if marker is not None:
                        current_marker = marker
            
            # Stocker le hint pour ce DocId
            pdatum_hint_by_docid[doc_id] = current_marker.isoformat() if current_marker is not None else ''
        
        self._pdatum_hint_by_docid = pdatum_hint_by_docid
        return pdatum_hint_by_docid
    
    # =========================================================================
    # STRING-TABLE-BASED PDatum EXTRACTION (Algorithm ST-based)
    # See readme/ALGORITHME_ST_BASED.md for full documentation.
    # =========================================================================
    
    # Regex patterns for ST-based classification
    _RE_ST_DOCID = re.compile(r'^[0-9a-f]{32}$')
    _RE_ST_NUM   = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')
    _RE_ST_DATE  = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
    _RE_ST_ZERO  = re.compile(r'^0{4}-0{2}-0{2}$')
    _RE_ST_GCODE = re.compile(r'^\d{3}$')
    
    def _extract_pdatum_from_string_table(
        self,
        payload: str,
    ) -> Dict[str, Dict[str, str]]:
        """Extract PDatum and EDatum for each item using string table order.
        
        This is the ST-based algorithm that achieves ~98% PDatum accuracy.
        It exploits the fact that string table order = UI item order, and
        PDatum appears once per wave between items at wave boundaries.
        
        Args:
            payload: Raw GWT //OK[...] response text
            
        Returns:
            Dict mapping DocId -> {pdatum, edatum, num, pdatum_source}
            Empty dict on failure (caller should fall back to legacy detection).
        """
        st = gwt_utils.extract_string_table(payload)
        if not st or len(st) < 20:
            logger.debug("ST-based extraction: no string table found")
            return {}
        
        try:
            items = self._st_extract_items(st)
        except Exception as exc:
            logger.warning("ST-based extraction failed: %s", exc, exc_info=True)
            return {}
        
        if not items:
            return {}
        
        result: Dict[str, Dict[str, str]] = {}
        for item in items:
            docid = item.get('docid', '')
            if docid:
                result[docid] = {
                    'pdatum': item.get('pdatum') or '',
                    'edatum': item.get('edatum') or '',
                    'num': item.get('num') or '',
                    'pdatum_source': item.get('pdatum_source', 'none'),
                }
        
        logger.info(
            "ST-based extraction: %d items, %d with PDatum",
            len(result),
            sum(1 for v in result.values() if v['pdatum']),
        )
        return result
    
    def _st_extract_items(self, st: List[str]) -> List[Dict[str, Any]]:
        """Core ST-based extraction algorithm.
        
        Algorithm:
        1. Classify ST entries (DOCID, NUM, DATE, GCODE, SEP, ZDATE)
        2. Pair each DOCID with its nearest following NUM
        3. For each item, EDatum = ST[NUM_idx + 1] if it's a DATE
        4. For each inter-item zone, find LAST non-EDatum non-future date
        5. If that date is strictly < current PDatum → new wave
        6. Items inherit current_pdatum until next wave change
        """
        # Step 1: Classify every entry
        classified: List[Tuple[int, str, str]] = []
        for idx, val in enumerate(st):
            if self._RE_ST_DOCID.match(val):
                classified.append((idx, 'DOCID', val))
            elif self._RE_ST_NUM.match(val):
                classified.append((idx, 'NUM', val))
            elif self._RE_ST_ZERO.match(val):
                classified.append((idx, 'ZDATE', val))
            elif self._RE_ST_DATE.match(val):
                classified.append((idx, 'DATE', val))
            elif val == '-':
                classified.append((idx, 'SEP', val))
            elif self._RE_ST_GCODE.match(val):
                classified.append((idx, 'GCODE', val))
        
        docid_entries = [(idx, val) for idx, typ, val in classified if typ == 'DOCID']
        num_entries   = [(idx, val) for idx, typ, val in classified if typ == 'NUM']
        date_entries  = [(idx, val) for idx, typ, val in classified if typ == 'DATE']
        
        # Step 2: Pair each DOCID with nearest following unused NUM
        items: List[Dict[str, Any]] = []
        used_nums: Set[int] = set()
        
        for didx, docid in docid_entries:
            best_num = None
            for nidx, num in num_entries:
                if nidx > didx and nidx not in used_nums:
                    best_num = (nidx, num)
                    break
            if best_num:
                used_nums.add(best_num[0])
                items.append({
                    'docid': docid,
                    'num': best_num[1],
                    'docid_idx': didx,
                    'num_idx': best_num[0],
                    'edatum': None,
                    'pdatum': None,
                    'pdatum_source': 'none',
                })
        
        if not items:
            return items
        
        # Step 3: EDatum = ST[NUM_idx + 1] if DATE (the "+1 rule")
        for item in items:
            ni = item['num_idx']
            if ni + 1 < len(st):
                candidate = st[ni + 1]
                if self._RE_ST_DATE.match(candidate):
                    item['edatum'] = candidate
        
        # Step 4-6: Detect PDatum waves by scanning inter-item zones
        scrape_date = self.today
        future_limit = scrape_date + timedelta(days=5)
        current_pdatum: Optional[str] = None
        
        for i in range(len(items)):
            num_idx = items[i]['num_idx']
            
            # Zone end = start of next item (considering GCODE prefix)
            if i + 1 < len(items):
                next_start = self._st_item_start_idx(items[i + 1], classified)
            else:
                next_start = len(st)
            
            # All dates in the zone after this item's NUM and before next item
            zone_dates = [(idx, val) for idx, val in date_entries
                          if num_idx < idx < next_start]
            
            # Filter: exclude EDatum, future dates, zero dates
            pdatum_candidates = []
            for idx, val in zone_dates:
                if val == items[i]['edatum']:
                    continue  # Skip this item's EDatum
                d = self._parse_date_any(val)
                if not d:
                    continue
                if d > future_limit:
                    continue  # Future date → appeal deadline or similar
                pdatum_candidates.append((idx, val))
            
            if pdatum_candidates:
                # Take the LAST candidate (closest to next item)
                new_pd = pdatum_candidates[-1][1]
                new_pd_date = self._parse_date_any(new_pd)
                cp = self._parse_date_any(current_pdatum) if current_pdatum else None
                
                if new_pd != current_pdatum:
                    if cp is None or (new_pd_date and new_pd_date < cp):
                        # New wave: strictly decreasing PDatum
                        current_pdatum = new_pd
                        items[i]['pdatum'] = current_pdatum
                        items[i]['pdatum_source'] = 'st_wave_change'
                    else:
                        # Date >= current → not a wave change (appeal date etc.)
                        items[i]['pdatum'] = current_pdatum
                        items[i]['pdatum_source'] = 'st_wave_inherit'
                else:
                    items[i]['pdatum'] = current_pdatum
                    items[i]['pdatum_source'] = 'st_explicit'
            else:
                # No date in zone → inherit current wave
                items[i]['pdatum'] = current_pdatum
                if items[i]['pdatum_source'] == 'none':
                    items[i]['pdatum_source'] = 'st_wave_inherit'
        
        return items
    
    @staticmethod
    def _st_item_start_idx(
        item: Dict[str, Any],
        classified: List[Tuple[int, str, str]],
    ) -> int:
        """Find earliest ST index for an item (GCode before DOCID, or DOCID itself)."""
        docid_idx = item['docid_idx']
        for idx, typ, _val in classified:
            if typ == 'GCODE' and idx < docid_idx and docid_idx - idx <= 5:
                return idx
        return docid_idx
    
    def _search_pdatum_in_content(
        self,
        doc_id: str,
        content: str,
        doc_id_pos: Optional[int] = None,
        next_doc_id_pos: Optional[int] = None,
        edatum: Optional[str] = None,
    ) -> Optional[str]:
        """Recherche PDatum dans le contenu brut GWT autour du DocId.
        
        C'est LA méthode clé pour Fribourg: les dates dans les tokens ne sont pas
        toujours fiables. On scanne le contenu brut dans une fenêtre BORNÉE entre
        DocId et le prochain DocId.
        
        Stratégie Fribourg (scoring):
        1. Borner la fenêtre strictement au prochain DocId (évite contamination cross-row)
        2. Exclure le EDatum connu (évite confusion EDatum/PDatum)
        3. Score = (-date_numeric, is_before, distance)
           - D'abord la date la plus récente (PDatum = date de publication, toujours récente)
           - Puis préférer dates APRÈS le DocId (is_before=0 meilleur que is_before=1)
           - Puis la plus proche du DocId (distance)
        """
        if not doc_id or not content:
            return None
        
        # Trouver la position du DocId si non fournie
        if doc_id_pos is None:
            pos = content.find(doc_id)
            if pos < 0:
                return None
            doc_id_pos = pos
        
        # CRITICAL: Borner la fenêtre au prochain DocId pour éviter contamination
        # Si next_doc_id_pos n'est pas fourni, chercher le prochain DocId dans le contenu
        row_end_pos = next_doc_id_pos
        if row_end_pos is None or row_end_pos <= doc_id_pos:
            # Chercher le prochain DocId dans le contenu
            probe_start = doc_id_pos + max(1, len(doc_id))
            probe_end = min(len(content), probe_start + 60000)
            probe = content[probe_start:probe_end]
            m = RE_ID.search(probe)
            if m is not None:
                row_end_pos = probe_start + m.start()
            else:
                row_end_pos = min(len(content), doc_id_pos + 6000)
        
        # Fribourg: fenêtre 800 chars avant le DocId (l'en-tête de section est
        # proche du premier item), jusqu'au prochain DocId.
        # Réduit de 3000→800 pour limiter le bruit (dates de sections/items voisins).
        start = max(0, doc_id_pos - 800)
        max_window = 22000
        end = min(row_end_pos, start + max_window)
        
        window = content[start:end]
        window = self._decode_context_text(window)
        
        # Point d'ancrage: position du DocId dans la fenêtre
        anchor = doc_id_pos - start
        
        # Collecter toutes les dates avec leur position
        matches_with_pos: List[Tuple[int, str]] = []
        
        # Dates ISO
        for m in RE_DATE_ISO.finditer(window):
            iso = m.group()
            d = self._parse_date_any(iso)
            if d is None:
                continue
            matches_with_pos.append((m.start(), iso))
        
        # Dates CH (DD.MM.YYYY)
        for m in RE_DATE_CH.finditer(window):
            d = self._parse_date_any(m.group())
            if d is None:
                continue
            matches_with_pos.append((m.start(), d.isoformat()))
        
        if not matches_with_pos:
            return None
        
        # Tri par position
        matches_with_pos.sort(key=lambda x: x[0])
        
        # Exclure le EDatum connu pour éviter la confusion EDatum/PDatum
        edatum_d = self._parse_date_any(edatum) if edatum else None
        
        # Appliquer le scoring Fribourg:
        # Score = (-date_numeric, is_before, distance)
        # La date RÉCENTE domine (PDatum est toujours >= EDatum).
        # En secondaire: position (après DocId légèrement préféré), puis distance.
        # Plus petit score = meilleur
        best: Optional[Tuple[Tuple[int, int, int], date]] = None
        
        for pos, iso in matches_with_pos:
            d = self._parse_date_any(iso)
            if d is None:
                continue
            
            # Exclure EDatum (correspondance exacte).
            # Note historique: ±1 jour excluait le PDatum correct quand
            # PDatum = EDatum+1 (ex: 601 2026 17, EDatum=02-09, PDatum=02-10).
            # La correspondance exacte suffit car le scoring favorise les dates
            # récentes (PDatum > EDatum), limitant le risque de confusion.
            if edatum_d and d == edatum_d:
                continue
            
            # Filtrer dates futures
            if self.future_cutoff_pd and d > self.future_cutoff_pd:
                continue
            # Filtrer avec max_date en mode incrémental
            if self.max_date and d > self.max_date:
                continue
            
            # Position relative au DocId
            rel = pos - anchor
            is_before = 1 if rel < 0 else 0  # 0 = après DocId (préféré), 1 = avant
            dist = abs(rel) if rel < 0 else rel
            
            # Score: (-date_numeric, is_before, distance)
            # Plus petit = meilleur (date plus récente, is_before=0 > is_before=1, distance plus courte)
            date_numeric = int(d.strftime('%Y%m%d'))
            score = (-date_numeric, is_before, dist)
            
            if best is None or score < best[0]:
                best = (score, d)
        
        if best is not None:
            return best[1].isoformat()
        
        return None
    
    def _detect_dates(
        self,
        tokens: List[str],
        id_pos: int,
        row_end_hint: Optional[int],
        doc_id: str,
        content: str,
        doc_id_pos: Optional[int],
        next_doc_id_pos: Optional[int],
        pdatum_hint: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Détecte EDatum et PDatum avec la logique complète Fribourg.
        
        Args:
            tokens: Liste des tokens de la ligne
            id_pos: Position du DocId dans les tokens
            row_end_hint: Position de fin de ligne (prochain DocId) ou None
            doc_id: Identifiant du document
            content: Contenu GWT brut complet
            doc_id_pos: Position du DocId dans le contenu brut
            next_doc_id_pos: Position du prochain DocId dans le contenu brut
            pdatum_hint: Date de publication propagée (marker) depuis un item précédent.
                        Si fourni, appliqué avec les règles upgrade/clamp/rescue.
                        Voir _compute_pdatum_hint_by_docid_for_page() pour le calcul.
        
        Returns:
            Tuple (edatum, pdatum) au format ISO 'YYYY-MM-DD' ou chaînes vides
        
        Stratégie en 6 étapes:
        1. Trouver EDatum depuis les tokens (position +3 après ID)
        2. Collecter les candidats PDatum depuis les tokens
        3. Appliquer la stratégie "last_in_row" (dernière date != EDatum)
        4. Override Fribourg: si raw content donne une date différente, la préférer
        5. Fallback: scan du contenu brut si pas de PDatum token
        6. Appliquer pdatum_hint (propagation marker) si disponible
        """
        edatum = ""
        pdatum = ""
        
        # Bornes de la ligne
        row_end = row_end_hint if isinstance(row_end_hint, int) and row_end_hint > 0 else len(tokens)
        row_end = min(row_end, len(tokens))
        
        # Note: GWT token layout varies between items:
        #   Layout A (with court name): DocId → court_name → Nummer → EDatum  (EDatum at id_pos+3)
        #   Layout B (without court):   DocId → Nummer → EDatum             (EDatum at id_pos+2)
        # Starting at id_pos+2 handles both layouts safely because Nummer
        # (format "NNN YYYY NN") never parses as a date.
        base_idx = id_pos + 2
        
        # ----- EDATUM -----
        for offset in [0, 1, 2]:
            idx = base_idx + offset
            if idx < row_end:
                tok = self._safe_get(tokens, idx, '')
                d = self._parse_date_any(tok)
                if d:
                    edatum = d.isoformat()
                    break
        
        # Scan élargi si non trouvé
        if not edatum:
            for off in range(0, min(20, max(0, row_end - base_idx))):
                idx = base_idx + off
                if idx < row_end:
                    d = self._parse_date_any(self._safe_get(tokens, idx, ''))
                    if d:
                        edatum = d.isoformat()
                        break
        
        # Filtrer EDatum futur
        if edatum:
            ed_obj = self._parse_date_any(edatum)
            if ed_obj and self.future_cutoff_ed and ed_obj > self.future_cutoff_ed:
                edatum = ""
        
        # ----- PDATUM: Stratégie last_in_row -----
        # Collecter les candidats depuis les tokens
        candidates: List[str] = []
        
        # Chercher d'abord en fin de ligne
        for ti in [row_end - 1, row_end - 2, row_end - 3]:
            if 0 <= ti < row_end:
                d = self._parse_date_any(self._safe_get(tokens, ti, ''))
                if d:
                    candidates.append(d.isoformat())
        
        # Si pas de candidats, scanner après la date de base
        if not candidates:
            for off in range(0, 8):
                idx = base_idx + 2 + off
                if idx < row_end:
                    d = self._parse_date_any(self._safe_get(tokens, idx, ''))
                    if d:
                        candidates.append(d.isoformat())
        
        # Stratégie last_in_row: parcourir depuis la fin
        picked_pdatum = ""
        for idx in range(row_end - 1, max(-1, id_pos - 1), -1):
            tok = self._safe_get(tokens, idx, '')
            d = self._parse_date_any(tok)
            if d is None:
                continue
            if self.future_cutoff_pd and d > self.future_cutoff_pd:
                continue
            iso = d.isoformat()
            if edatum and iso == edatum:
                continue
            picked_pdatum = iso
            break
        
        pdatum = picked_pdatum
        
        # ----- OVERRIDE FRIBOURG: Préférer le raw content scan -----
        # Le raw content est plus fiable car il correspond mieux à l'UI
        pdatum_from_raw_content = False
        fb_pdatum = self._search_pdatum_in_content(
            doc_id, content, doc_id_pos, next_doc_id_pos,
            edatum=edatum
        )

        if fb_pdatum:
            fb_d = self._parse_date_any(fb_pdatum)
            if fb_d is not None:
                # Raw fallback gagne (même si plus ancien) - correspond mieux à l'UI
                pdatum = fb_d.isoformat()
                pdatum_from_raw_content = True

        # ----- FALLBACK si toujours pas de PDatum -----
        if not pdatum and doc_id and content:
            fallback = self._search_pdatum_in_content(
                doc_id, content, doc_id_pos, next_doc_id_pos,
                edatum=edatum
            )
            if fallback:
                pdatum = fallback
                pdatum_from_raw_content = True
        
        # ----- PDATUM HINT: Propagation du marker -----
        # Le hint est calculé par _compute_pdatum_hint_by_docid_for_page()
        # Il représente la "date de publication" propagée depuis un DocId précédent
        # qui avait 2+ dates distinctes dans son chunk.
        #
        # Règles d'application:
        # 1. Upgrade only: si le hint est plus récent que le pdatum actuel, on l'applique
        # 2. Si pas de pdatum mais un hint, on applique le hint
        # 3. Clamp: si pdatum > hint, on clamp au hint (évite les dates "juridiques" plus récentes)
        if pdatum_hint:
            hint_d = self._parse_date_any(pdatum_hint)
            cur_d = self._parse_date_any(pdatum) if pdatum else None
            
            if hint_d is not None:
                # Vérifier que le hint respecte les limites
                if self.future_cutoff_pd and hint_d > self.future_cutoff_pd:
                    hint_d = None
                if hint_d and self.max_date and hint_d > self.max_date:
                    hint_d = None
            
            if hint_d is not None:
                apply_hint = False

                # Cas 1: Pas de pdatum actuel -> appliquer le hint
                if cur_d is None:
                    apply_hint = True
                # Cas 2: Le hint est plus récent -> upgrade
                # Le hint représente la date de publication de la "vague" (section header).
                # Le raw content peut se tromper (date de décision, date juridique, etc.).
                #
                # Règle: on upgrade avec le hint si:
                #   a) Le raw content a trouvé une date ≤ EDatum → confusion quasi-certaine
                #   b) Le raw content n'a pas de haute confiance (pas de date du raw)
                #   c) L'écart hint/raw est > 5 jours → le raw a probablement raté la section
                #
                # Known limitation: This upgrade causes 4 items to "slide" to wrong dates
                # (~9% error). However, disabling it causes 13+ items to lose correct dates
                # (accuracy drops to 70.5%). The upgrade is a net positive.
                # See PDATUM_STATUS_2026-02-16.md for details.
                elif hint_d > cur_d:
                    ed_obj = self._parse_date_any(edatum) if edatum else None
                    if pdatum_from_raw_content:
                        if ed_obj and cur_d <= ed_obj:
                            # Raw content a trouvé EDatum ou + ancien → confusion
                            apply_hint = True
                        elif (hint_d - cur_d).days > 5:
                            # Écart significatif: le raw n'a pas trouvé la bonne section
                            apply_hint = True
                        elif self.min_date and cur_d < self.min_date and hint_d >= self.min_date:
                            apply_hint = True  # Rescue: date trop ancienne pour --days
                        else:
                            apply_hint = False  # Raw content plausible, garder
                    else:
                        # Date basse confiance: upgrader avec le hint
                        apply_hint = True
                # Cas 3: En mode --days, remplacer un pdatum trop ancien par le hint
                elif self.min_date and cur_d < self.min_date and hint_d >= self.min_date:
                    apply_hint = True

                if apply_hint:
                    pdatum = hint_d.isoformat()

                # Clamp: si pdatum > hint, réduire au hint (évite dates juridiques)
                # Le clamp s'applique même pour les dates raw content car une date
                # plus récente que le hint est probablement juridique (audience etc.)
                if pdatum:
                    final_d = self._parse_date_any(pdatum)
                    if final_d and final_d > hint_d:
                        pdatum = hint_d.isoformat()
        
        # Valider avec max_date si mode incrémental
        if self.max_date:
            if pdatum:
                pd_d = self._parse_date_any(pdatum)
                if pd_d and pd_d > self.max_date:
                    pdatum = ""
            if edatum:
                ed_d = self._parse_date_any(edatum)
                if ed_d and ed_d > self.max_date:
                    edatum = ""
        
        return edatum, pdatum
    
    # =========================================================================
    # PAGE PARSING
    # =========================================================================
    
    async def parse_page(self, response: Response):
        """Parse une page de résultats GWT."""
        current_page = response.meta.get('page_nr', 0)
        is_warmup = response.meta.get('warmup', False)
        
        logger.info(f"Page {current_page} reçue (status={response.status})")
        
        # Vérifier erreur GWT
        body_preview = (response.text or '')[:500]
        if 'IncompatibleRemoteServiceException' in body_preview or 'strongName' in body_preview:
            if self._bootstrap_retry_count < self.MAX_BOOTSTRAP_RETRIES:
                self._bootstrap_retry_count += 1
                logger.warning(f"Erreur RPC, re-bootstrap {self._bootstrap_retry_count}")
                bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url')
                self.page_nr = current_page
                yield scrapy.Request(
                    url=bootstrap_url,
                    method="GET",
                    headers=self.headers,
                    callback=self.parse_bootstrap,
                    errback=self.errback_general,
                    meta={'referrer_policy': 'no-referrer'},
                    dont_filter=True
                )
                return
            else:
                logger.error("Erreur RPC persistante, arrêt")
                return
        
        # Vérifier response valide
        if not (response.status == 200 and len(response.body) >= MINIMUM_PAGE_LEN):
            logger.error(f"Réponse invalide: status={response.status}, len={len(response.body)}")
            return
        
        # Extraire trefferzahl
        if current_page == 0 or self.trefferzahl == 0:
            tz = gwt_utils.extract_trefferzahl(response.text)
            if tz is not None:
                self.trefferzahl = tz
                logger.info(f"Total résultats: {self.trefferzahl}")
        
        if self.trefferzahl <= 0:
            logger.warning("0 résultats trouvés")
            return
        
        # Si warmup, relancer avec le bon body
        if is_warmup:
            body = self._build_request_body(page_nr=0, use_sort=True)
            self.page_nr = 1
            yield scrapy.Request(
                url=self.result_page_url,
                method="POST",
                body=body,
                headers=self.headers,
                callback=self.parse_page,
                errback=self.errback_general,
                meta={'page_nr': 0},
                dont_filter=True
            )
            return
        
        self.pages_processed += 1
        self._page_pending_docids[current_page] = set()
        
        # Parser le contenu GWT
        content = gwt_utils.strip_gwt_prefix(response.text)
        werte = gwt_utils.extract_tokens(response.text)
        
        if not werte or len(werte) < 9:
            logger.warning(f"Page {current_page}: tokens insuffisants ({len(werte) if werte else 0})")
            return
        
        # Trouver les DocIds
        id_indexes = gwt_utils.find_docid_indexes(werte, RE_ID)
        docid_positions = self._collect_docid_positions(content)
        
        logger.info(f"Page {current_page}: {len(id_indexes)} DocIds détectés")
        
        if not id_indexes:
            logger.warning("Aucun DocId trouvé, fin de pagination")
            return
        
        # Map des prochains DocIds pour borner les lignes
        next_doc_idx_map = self._build_next_doc_idx_map(id_indexes)
        
        # =====================================================================
        # PRIMARY: ST-based PDatum extraction (see readme/ALGORITHME_ST_BASED.md)
        # Uses string table order to detect PDatum waves with ~98% accuracy.
        # Falls back to legacy marker propagation if ST extraction fails.
        # =====================================================================
        st_results = self._extract_pdatum_from_string_table(response.text)
        
        # FALLBACK: Legacy marker propagation (used when ST-based misses items)
        pdatum_hint_map = self._compute_pdatum_hint_by_docid_for_page(
            content=content,
            werte=werte,
            id_indexes=id_indexes,
            docid_positions=docid_positions,
        )
        
        # Traiter chaque DocId
        eligible_count = 0
        older_count = 0
        st_used_count = 0
        legacy_used_count = 0
        limit_ids = min(len(id_indexes), MAX_IDS_PER_PAGE)
        
        for i, idx in enumerate(id_indexes[:limit_ids]):
            if not self._can_emit():
                logger.info(f"Cap MAX_ITEMS atteint ({self.MAX_ITEMS})")
                break
            
            doc_id = self._safe_get(werte, idx, '')
            if not doc_id or doc_id in self.seen_ids:
                continue
            self.seen_ids.add(doc_id)
            
            # Position dans le contenu brut
            positions = docid_positions.get(doc_id, [])
            doc_id_pos = positions[0] if positions else None
            
            # Position du prochain DocId pour borner la ligne
            next_idx = next_doc_idx_map.get(idx)
            next_doc_id_pos = None
            if next_idx is not None:
                next_doc_id = self._safe_get(werte, next_idx, '')
                if next_doc_id:
                    next_positions = docid_positions.get(next_doc_id, [])
                    if next_positions:
                        next_doc_id_pos = next_positions[0]
            
            # --- DATE DETECTION: ST-based primary, legacy fallback ---
            st_info = st_results.get(doc_id)
            
            if st_info and st_info.get('pdatum'):
                # ST-based extraction succeeded → use as primary source
                pdatum = st_info['pdatum']
                edatum = st_info.get('edatum') or ''
                st_used_count += 1
                
                # If ST didn't find EDatum (deduplication), fall back to
                # legacy token-based EDatum detection
                if not edatum:
                    # Fenêtre de tokens pour EDatum detection
                    win_start = max(0, idx - ROW_WINDOW_BEFORE)
                    win_end = min(len(werte), (next_idx if next_idx else idx + ROW_WINDOW_AFTER))
                    if next_idx and next_idx > win_end and next_idx - win_start <= 900:
                        win_end = min(len(werte), next_idx)
                    slice_tokens = werte[win_start:win_end]
                    id_hint = idx - win_start
                    row_end_hint = (next_idx - win_start) if next_idx else None
                    
                    edatum_legacy, _ = self._detect_dates(
                        slice_tokens, id_hint, row_end_hint,
                        doc_id, content, doc_id_pos, next_doc_id_pos,
                        pdatum_hint=None
                    )
                    if edatum_legacy:
                        edatum = edatum_legacy
            else:
                # ST-based extraction failed for this DocId → full legacy path
                legacy_used_count += 1
                
                win_start = max(0, idx - ROW_WINDOW_BEFORE)
                win_end = min(len(werte), (next_idx if next_idx else idx + ROW_WINDOW_AFTER))
                if next_idx and next_idx > win_end and next_idx - win_start <= 900:
                    win_end = min(len(werte), next_idx)
                
                slice_tokens = werte[win_start:win_end]
                id_hint = idx - win_start
                row_end_hint = (next_idx - win_start) if next_idx else None
                
                pdatum_hint = pdatum_hint_map.get(doc_id, '') or None
                
                edatum, pdatum = self._detect_dates(
                    slice_tokens, id_hint, row_end_hint,
                    doc_id, content, doc_id_pos, next_doc_id_pos,
                    pdatum_hint=pdatum_hint
                )
            
            # Vérifier éligibilité (mode incrémental)
            is_eligible = True
            cutoff_date_str = pdatum or edatum
            cutoff_date_obj = self._parse_date_any(cutoff_date_str) if cutoff_date_str else None
            
            if self.min_date and cutoff_date_obj:
                if cutoff_date_obj < self.min_date:
                    is_eligible = False
                    older_count += 1
            
            if self.min_date and cutoff_date_obj is None:
                # Sans date détectée en mode incrémental, on ne peut pas valider
                is_eligible = False
            
            if not is_eligible:
                continue
            
            eligible_count += 1
            
            # Parser la ligne et créer l'item/request
            # Ensure slice_tokens/id_hint/row_end_hint are available for _parse_row
            # (they are computed in both ST-based and legacy paths above, except
            #  the ST-based path with EDatum found — compute them now if missing)
            if st_info and st_info.get('pdatum') and st_info.get('edatum'):
                # ST path found both dates; compute token window for _parse_row
                win_start = max(0, idx - ROW_WINDOW_BEFORE)
                win_end = min(len(werte), (next_idx if next_idx else idx + ROW_WINDOW_AFTER))
                if next_idx and next_idx > win_end and next_idx - win_start <= 900:
                    win_end = min(len(werte), next_idx)
                slice_tokens = werte[win_start:win_end]
                id_hint = idx - win_start
                row_end_hint = (next_idx - win_start) if next_idx else None
            
            result = self._parse_row(
                slice_tokens, content, current_page, doc_id, id_hint,
                edatum, pdatum, doc_id_pos, row_end_hint
            )
            
            if result is None:
                continue
            
            # Enregistrer dans la barrier
            self._barrier_register(current_page, doc_id)
            
            yield result
        
        logger.info(f"Page {current_page}: {eligible_count} éligibles, {older_count} trop anciens (ST={st_used_count}, legacy={legacy_used_count})")
        
        # Heuristique d'arrêt pour mode incrémental
        if self.min_date and eligible_count == 0:
            logger.info("Arrêt: aucun item éligible sur cette page")
            self._older_seen = True
        
        if self._older_seen:
            logger.info("Arrêt pagination: items plus anciens que min_date")
            return
        
        # Pagination
        should_continue = True
        if self.MAX_PAGES > 0 and self.pages_processed >= self.MAX_PAGES:
            should_continue = False
        if self.trefferzahl and len(self.seen_ids) >= self.trefferzahl:
            should_continue = False
        if not self._can_emit():
            should_continue = False
        
        if should_continue:
            body = self._build_request_body(page_nr=self.page_nr, use_sort=bool(self.days))
            next_page_nr = self.page_nr
            self.page_nr += 1
            
            next_req = scrapy.Request(
                url=self.result_page_url,
                method="POST",
                body=body,
                headers=self.headers,
                callback=self.parse_page,
                errback=self.errback_general,
                meta={'page_nr': next_page_nr},
                dont_filter=True
            )
            
            # Barrier: différer si DocIds en cours de traitement PDF
            if self.PAGE_PDF_BARRIER:
                pending = self._page_pending_docids.get(current_page, set())
                if pending:
                    self._page_next_request[current_page] = next_req
                    logger.info(f"Barrier: attente PDFs page {current_page} ({len(pending)} pending)")
                else:
                    yield next_req
            else:
                yield next_req
        else:
            logger.info(f"Fin scraping: {self.pages_processed} pages, {self.items_emitted} items")
    
    def _parse_row(
        self,
        tokens: List[str],
        content: str,
        page_nr: int,
        doc_id: str,
        id_hint: int,
        edatum: str,
        pdatum: str,
        doc_id_pos: Optional[int],
        row_end_hint: Optional[int],
    ):
        """Parse une ligne GWT et retourne une Request decrypt ou un Item."""
        try:
            # Numéro de dossier
            num = self._detect_num(tokens, id_hint)
            
            # Titre (position après ID)
            titel_idx = id_hint + 1
            titel = self._safe_get(tokens, titel_idx, '').replace("\\x27", "'")
            if len(titel) < 8:
                titel = ""
            
            # Leitsatz
            leitsatz = ""
            for offset in range(5, 15):
                tok = self._safe_get(tokens, id_hint + offset, '')
                if len(tok) > 20 and not RE_ID.fullmatch(tok):
                    # Vérifier que ce n'est pas un chemin PDF ou autre pattern
                    if not RE_PFAD.fullmatch(tok) and not RE_PFAD2.fullmatch(tok):
                        leitsatz = tok.replace("\\x27", "'")
                        break
            
            # Chemin PDF
            pfad, neue_syntax = self._detect_pdf_path(tokens, doc_id, content, doc_id_pos)
            
            # Créer l'item
            item = PublicationItem()
            item['Kanton'] = self.kanton_kurz
            item['DocId'] = doc_id
            item['Num'] = num
            item['Signatur'] = ''
            item['Gericht'] = ''
            item['Kammer'] = ''
            item['VGericht'] = ''
            item['Titel'] = titel
            item['Leitsatz'] = leitsatz
            item['EDatum'] = edatum if edatum != '0000-00-00' else ''
            item['PDatum'] = pdatum if pdatum != '0000-00-00' else ''
            item['Rechtsgebiet'] = ''
            item['VKammer'] = ''
            item['HTMLUrls'] = []
            item['PDFUrls'] = []
            item['Raw'] = ''
            item['PageNr'] = page_nr
            
            # Si pas de pfad, retourner l'item sans PDF
            if not pfad:
                logger.warning(f"DocId {doc_id}: pas de chemin PDF détecté")
                if not self._can_emit():
                    return None
                self._record_emit(doc_id)
                return item
            
            # Construire le body decrypt
            numstr = num.replace(' ', '_')
            decrypt_body = self._build_decrypt_body(pfad, numstr, neue_syntax)
            
            return scrapy.Request(
                url=self.decrypt_page_url,
                method="POST",
                body=decrypt_body,
                headers=self.headers,
                callback=self.decrypt_path,
                errback=self.errback_decrypt,
                meta={'item': item, 'page_nr': page_nr},
                dont_filter=True
            )
            
        except Exception as e:
            logger.error(f"Erreur parsing row {doc_id}: {e}", exc_info=True)
            return None
    
    # =========================================================================
    # PDF DECRYPT & VERIFY
    # =========================================================================
    
    def _detect_pdf_path(
        self,
        tokens: List[str],
        doc_id: str,
        content: str,
        doc_id_pos: Optional[int],
    ) -> Tuple[Optional[str], bool]:
        """Détecte le chemin PDF dans les tokens ou le contenu."""
        neue_syntax = False
        pfad = None
        
        # Chercher dans les tokens
        for tok in tokens:
            if not tok:
                continue
            if RE_PFAD.fullmatch(tok):
                pfad = tok
                break
            elif RE_PFAD2.fullmatch(tok):
                pfad = tok
                neue_syntax = True
                break
        
        # Fallback: chercher dans le contenu brut autour du DocId
        if not pfad and doc_id and content:
            pfad, neue_syntax = self._search_pdf_path_in_content(doc_id, content, doc_id_pos)
        
        return pfad, neue_syntax
    
    def _search_pdf_path_in_content(
        self,
        doc_id: str,
        content: str,
        doc_id_pos: Optional[int],
    ) -> Tuple[Optional[str], bool]:
        """Recherche un chemin PDF dans le contenu GWT autour du DocId."""
        if not doc_id or not content:
            return None, False
        
        # Trouver toutes les occurrences du DocId
        occurrences = []
        if doc_id_pos is not None:
            occurrences.append(doc_id_pos)
        
        search_start = 0
        while True:
            idx = content.find(doc_id, search_start)
            if idx == -1:
                break
            if idx not in occurrences:
                occurrences.append(idx)
            search_start = idx + len(doc_id)
        
        # Chercher dans les fenêtres autour de chaque occurrence
        for idx in occurrences:
            # Fenêtre après
            window_after = content[idx:min(len(content), idx + 900)]
            for pattern, flag in ((RE_PFAD, False), (RE_PFAD2, True)):
                m = pattern.search(window_after)
                if m:
                    return m.group(0), flag
            
            # Fenêtre avant
            window_before = content[max(0, idx - 600):idx]
            for pattern, flag in ((RE_PFAD, False), (RE_PFAD2, True)):
                m = pattern.search(window_before)
                if m:
                    return m.group(0), flag
        
        return None, False
    
    def _build_decrypt_body(self, pfad: str, numstr: str, neue_syntax: bool) -> str:
        """Construit le body pour la requête de décryptage."""
        if neue_syntax:
            pfad_encrypt = f"{numstr}_{pfad}|dossiernummer|{numstr}"
        elif self.ASCII_ENCRYPTED:
            ascii_pfad = ''
            for c in pfad:
                ascii_pfad += '|' + str(ord(c))
            pfad_encrypt = ascii_pfad.replace("|92|92", "|92")
        else:
            pfad_encrypt = pfad
        
        return self.DECRYPT_START + pfad_encrypt + self.DECRYPT_END
    
    async def decrypt_path(self, response: Response):
        """Callback decrypt: construit les URLs candidates et vérifie."""
        item = response.meta['item']
        page_nr = response.meta.get('page_nr', 0)
        doc_id = item['DocId']
        
        logger.debug(f"Decrypt pour {doc_id}")
        
        if response.status != 200:
            logger.error(f"Erreur decrypt: status={response.status}")
            async for out in self._emit_item_no_pdf(item, page_nr):
                yield out
            return
        
        # Construire les candidats PDF
        dossier = (item.get('Num') or '').replace(' ', '_')
        base = self.config.get('base_url', '').rstrip('/') or self.download_url.rstrip('/')
        
        result = gwt_utils.build_pdf_candidates_from_decrypt(
            response_text=response.text,
            download_url=self.download_url,
            base_url=base,
            dossier_fallback=dossier,
        )
        
        if not result:
            logger.error(f"Décryptage échoué pour {doc_id}")
            async for out in self._emit_item_no_pdf(item, page_nr):
                yield out
            return
        
        first_url, candidates = result
        logger.debug(f"Premier candidat PDF: {first_url}")
        
        yield scrapy.Request(
            url=first_url,
            method="GET",
            headers=self.headers,
            callback=self.verify_pdf_candidate,
            errback=self.errback_pdf,
            meta={
                'item': item,
                'candidates': candidates,
                'page_nr': page_nr,
                'handle_httpstatus_list': [400, 401, 403, 404, 410, 500, 502, 503],
            },
            dont_filter=True
        )
    
    async def verify_pdf_candidate(self, response: Response):
        """Vérifie si la réponse est un PDF valide."""
        item = response.meta['item']
        candidates = response.meta.get('candidates', [])
        page_nr = response.meta.get('page_nr', 0)
        doc_id = item['DocId']
        
        if gwt_utils.is_pdf_ok(response):
            # PDF trouvé!
            if not self._can_emit():
                return
            if doc_id in self._yielded_docids:
                return
            
            item['PDFUrls'] = [response.url]
            logger.info(f"PDF trouvé: {response.url}")
            
            self._record_emit(doc_id)
            yield item
            
            next_req = self._barrier_done(page_nr, doc_id)
            if next_req:
                yield next_req
            return
        
        # Essayer le candidat suivant
        if candidates:
            next_url = candidates.pop(0)
            logger.debug(f"Candidat suivant: {next_url}")
            
            yield scrapy.Request(
                url=next_url,
                method="GET",
                headers=self.headers,
                callback=self.verify_pdf_candidate,
                errback=self.errback_pdf,
                meta={
                    'item': item,
                    'candidates': candidates,
                    'page_nr': page_nr,
                    'handle_httpstatus_list': [400, 401, 403, 404, 410, 500, 502, 503],
                },
                dont_filter=True
            )
            return
        
        # Aucun candidat valide
        logger.warning(f"Aucun PDF valide pour {doc_id}")
        async for out in self._emit_item_no_pdf(item, page_nr):
            yield out
    
    async def _emit_item_no_pdf(self, item: Dict, page_nr: int):
        """Émet un item sans PDF et débloque la barrier."""
        doc_id = item.get('DocId')
        
        if not self._can_emit():
            next_req = self._barrier_done(page_nr, doc_id)
            if next_req:
                yield next_req
            return
        
        if doc_id and doc_id in self._yielded_docids:
            next_req = self._barrier_done(page_nr, doc_id)
            if next_req:
                yield next_req
            return
        
        item['PDFUrls'] = []
        if doc_id:
            self._record_emit(doc_id)
        yield item
        
        next_req = self._barrier_done(page_nr, doc_id)
        if next_req:
            yield next_req
    
    # =========================================================================
    # BARRIER - Synchronisation pagination/PDF (fan-out/join pattern)
    # =========================================================================
    # La barrier empêche la pagination tant que tous les PDFs de la page
    # courante ne sont pas traités. Cela évite de surcharger le serveur
    # et garantit un traitement ordonné page par page.
    
    def _barrier_register(self, page_nr: int, doc_id: str) -> None:
        """Enregistre un DocId comme en attente de PDF."""
        if not self.PAGE_PDF_BARRIER:
            return
        if page_nr not in self._page_pending_docids:
            self._page_pending_docids[page_nr] = set()
        self._page_pending_docids[page_nr].add(doc_id)
    
    def _barrier_done(self, page_nr: int, doc_id: Optional[str]) -> Optional[Request]:
        """Marque un DocId comme terminé et retourne la next_request si tous sont terminés."""
        if not self.PAGE_PDF_BARRIER:
            return None
        
        pending = self._page_pending_docids.get(page_nr, set())
        if doc_id and doc_id in pending:
            pending.discard(doc_id)
        
        if not pending:
            next_req = self._page_next_request.pop(page_nr, None)
            if next_req:
                logger.info(f"Barrier page {page_nr}: tous les PDFs traités, pagination")
                return next_req
        
        return None
    
    # =========================================================================
    # ERRBACKS - Gestion des erreurs réseau et serveur
    # =========================================================================
    # Ces handlers capturent les erreurs de requêtes pour:
    # - Logger le problème
    # - Émettre l'item sans PDF si possible
    # - Débloquer la barrier pour permettre la pagination
    
    async def errback_general(self, failure):
        """Errback générique pour les erreurs de requête."""
        logger.error(f"Erreur requête: {failure}")
    
    async def errback_decrypt(self, failure):
        """Errback decrypt."""
        req = getattr(failure, 'request', None)
        meta = getattr(req, 'meta', {}) if req else {}
        item = meta.get('item')
        page_nr = meta.get('page_nr', 0)
        
        logger.error(f"Erreur decrypt: {failure}")
        
        if item:
            async for out in self._emit_item_no_pdf(item, page_nr):
                yield out
    
    async def errback_pdf(self, failure):
        """Errback PDF candidate."""
        req = getattr(failure, 'request', None)
        meta = getattr(req, 'meta', {}) if req else {}
        item = meta.get('item')
        candidates = meta.get('candidates', [])
        page_nr = meta.get('page_nr', 0)
        
        logger.debug(f"Erreur PDF candidate: {failure}")
        
        # Essayer le suivant
        if candidates and item:
            next_url = candidates.pop(0)
            
            yield scrapy.Request(
                url=next_url,
                method="GET",
                headers=self.headers,
                callback=self.verify_pdf_candidate,
                errback=self.errback_pdf,
                meta={
                    'item': item,
                    'candidates': candidates,
                    'page_nr': page_nr,
                    'handle_httpstatus_list': [400, 401, 403, 404, 410, 500, 502, 503],
                },
                dont_filter=True
            )
            return
        
        if item:
            async for out in self._emit_item_no_pdf(item, page_nr):
                yield out
    
    # =========================================================================
    # HELPERS - Fonctions utilitaires
    # =========================================================================
    
    def _can_emit(self) -> bool:
        """Vérifie si on peut encore émettre des items (respect MAX_ITEMS)."""
        if self.MAX_ITEMS <= 0:
            return True
        return self.items_emitted < self.MAX_ITEMS
    
    def _record_emit(self, doc_id: str) -> None:
        """Enregistre l'émission d'un item."""
        if doc_id:
            self._yielded_docids.add(doc_id)
        self.items_emitted += 1
    
    def _safe_get(self, tokens: List[str], idx: int, default: str = "") -> str:
        """Accès sécurisé aux tokens."""
        try:
            return tokens[idx] if 0 <= idx < len(tokens) else default
        except Exception:
            return default
    
    def _collect_docid_positions(self, content: str) -> Dict[str, List[int]]:
        """Collecte les positions des DocIds dans le contenu."""
        positions: Dict[str, List[int]] = {}
        if not content:
            return positions
        for match in RE_ID.finditer(content):
            doc_id = match.group()
            positions.setdefault(doc_id, []).append(match.start())
        return positions
    
    def _build_next_doc_idx_map(self, id_indexes: List[int]) -> Dict[int, Optional[int]]:
        """Construit une map idx -> prochain idx dans la liste."""
        result: Dict[int, Optional[int]] = {}
        for i, idx in enumerate(id_indexes):
            result[idx] = id_indexes[i + 1] if i + 1 < len(id_indexes) else None
        return result
    
    def _detect_num(self, tokens: List[str], id_pos: int) -> str:
        """Détecte le numéro de dossier."""
        # Chercher dans les positions adjacentes (priorité: +2, +3, +1, +4, -1, -2)
        for offset in [2, 3, 1, 4, 0, -1, -2, 5, -3]:
            idx = id_pos + offset
            tok = self._safe_get(tokens, idx, '')
            if not tok:
                continue
            for pattern in NUM_PATTERNS:
                if pattern.fullmatch(tok):
                    return tok
        
        # Essayer de joindre plusieurs tokens
        for span in range(2, MAX_JOINED_TOKENS + 1):
            for offset in [2, 3, 1, 4]:
                start = id_pos + offset
                end = start + span
                if end > len(tokens):
                    continue
                joined = ' '.join(t for t in tokens[start:end] if t)
                if not joined:
                    continue
                for pattern in NUM_PATTERNS:
                    if pattern.fullmatch(joined):
                        return joined
        
        return ""
    
    def closed(self, reason):
        """Appelé à la fermeture du spider."""
        logger.info(f"Spider fermé: {reason}")
        logger.info(f"Stats: {self.pages_processed} pages, {self.items_emitted} items émis, {len(self.seen_ids)} DocIds vus")
