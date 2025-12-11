import scrapy
import re
import json
import time
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Iterable, Union, Tuple
from scrapy.http import Response, Request
from dotenv import load_dotenv
from publication_scraper.items import PublicationItem

# --- Module constants & regexes (for clarity and reuse) ---
# GWT response cleanup and token extraction
RE_VOR = re.compile(r'//OK\[[0-9,\.]+\[')
RE_ALL = re.compile(r'(?<=,\")[^\"]*(?:\\\"[^\"]*)*(?=\",)')
RE_ID = re.compile(r'[0-9a-f]{32}|[0-9]{15,17}')
RE_DATUM = re.compile(r'\d{4}-\d{2}-\d{2}')
RE_RG = re.compile(r'[^0-9\.:\-]{3}.{3,}')
RE_TREFFER = re.compile(r'(?<=^//OK\[)[0-9]+')
RE_DECRYPT = re.compile(r'(?<=//OK\[1,\[")[0-9a-f]+')
RE_DECRYPT2 = re.compile(r'(?<=//OK)([0-9,"a-z.A-Z/\[\]]+partURL\",\")(\?P<p1>[^\"]+_)(\?P<p2>[^\"_]+)\",\"(\?P<p3>dossiernummer)\",\"(\?P<p4>[^\"]+)')
RE_PFAD = re.compile(r'[A-Z]:(?:\\.+)+\.pdf')
RE_PFAD2 = re.compile(r'[0-9a-f]{128,192}')
# Optional/legacy
RE_DECODE = re.compile(r'\\x([0-9A-Fa-f]{2})')

RE_NUM = re.compile(r'.+')

# Dossier number patterns
RE_NUM1 = re.compile(r'^\d{3,4}\s+\d{4}\s+\d+$')
RE_NUM2 = re.compile(r'^\d{3,4}_\d{4}_\d+$')

# Heuristics
ROW_WINDOW_BEFORE = 25
ROW_WINDOW_AFTER = 60
MAX_IDS_PER_PAGE = 25
MINIMUM_PAGE_LEN = 148  # Minimum response length to consider a page "valid" for parsing

# Charger les variables d'environnement
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)


class TribunaSpider(scrapy.Spider):
    """
    Spider pour extraire les publications des sites Tribuna (Fribourg, Graubünden, etc.)
    
    Basé sur le modèle tribuna.py du client, utilise POST requests directes
    au lieu de Playwright pour parser les réponses GWT.
    
    Usage:
        # Scraping initial (toutes les décisions)
        scrapy crawl tribuna -a canton=fribourg
        
        # Scraping incrémental (depuis une date)
        scrapy crawl tribuna -a canton=fribourg -a ab=2024-11-01
    """
    name = 'tribuna'
    
    # Regex pour parser les réponses GWT (inspiré de tribuna.py) - reuse module-level constants
    reVor = RE_VOR
    reAll = RE_ALL
    reID = RE_ID
    reDatum = RE_DATUM
    reRG = RE_RG
    reTreffer = RE_TREFFER
    reDecrypt = RE_DECRYPT
    reDecrypt2 = RE_DECRYPT2
    rePfad = RE_PFAD
    rePfad2 = RE_PFAD2
    reDecode = RE_DECODE
    reNum = RE_NUM  # Regex pour numéro de dossier (à adapter selon canton)
    
    # Compteurs
    page_nr = 0
    trefferzahl = 0
    
    def __init__(self, canton='fribourg', ab=None, days=None, sort=None, *args, **kwargs):
        """
        Initialisation du spider
        
        Args:
            canton: Nom du canton (fribourg, graubunden, etc.)
            ab: Date de début pour scraping incrémental (format YYYY-MM-DD)
        """
        super().__init__(*args, **kwargs)
        
        # Charger la configuration du canton
        config_path = Path(__file__).parent.parent / 'cantons_config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        
        if canton not in configs:
            raise ValueError(f"Canton '{canton}' non trouvé dans la configuration. Cantons disponibles: {list(configs.keys())}")
        
        self.config = configs[canton]
        self.canton = canton
        self.kanton_kurz = self.config['kanton_kurz']
        self.ab = ab or os.getenv('START_DATE', None)
        # Mode mise à jour: --days=N (prioritaire sur START_DATE)
        self.days = int(days) if days is not None else None
        self.min_date = None
        if self.days is not None and self.days > 0:
            # today inclusive
            self.min_date = (datetime.utcnow() - timedelta(days=self.days)).date()
        # Sort mode: default to 'publication' if days is set and no explicit sort provided
        self.sort = sort
        if self.sort is None and self.days is not None and self.days > 0:
            self.sort = 'publication'
        
        # URLs et configuration
        self.RESULT_PAGE_URL = self.config['result_page_url']
        self.DECRYPT_PAGE_URL = self.config['decrypt_page_url']
        self.COOKIE_INIT = self.config.get('cookie_init_url')
        self.DOWNLOAD_URL = self.config['download_url']
        self.PDF_PATH = self.config['pdf_path']
        
        # Templates de requête
        self.RESULT_QUERY_TPL = self.config['result_query_tpl']
        self.RESULT_QUERY_TPL_AB = self.config['result_query_tpl_ab']
        self.DECRYPT_START = self.config['decrypt_start']
        self.DECRYPT_END = self.config['decrypt_end']
        self.PDF_PATTERN = self.config['pdf_pattern']
        
        # Flags
        self.ENCRYPTED = self.config['encrypted']
        self.ASCII_ENCRYPTED = self.config['ascii_encrypted']
        self.COOKIE = self.config['needs_cookie']
        self.HOLE_AUCH_HTML = self.config['hole_auch_html']
        # Behavioral knobs (can be provided in canton config)
        # Fraction of dated items on a page that must be older than min_date to trigger early-stop
        try:
            self.PAGE_OLDER_FRACTION = float(self.config.get('page_older_fraction', 0.75))
        except Exception:
            self.PAGE_OLDER_FRACTION = 0.75
        # If True, avoid issuing decrypt requests for rows whose detected PDatum is older than min_date
        self.DEFER_DECRYPT = bool(self.config.get('defer_decrypt', True))
        # Future date tolerance (days) is fixed to 0: publications with PDatum/EDatum > today
        # are considered future and will not be counted for cutoff heuristics.
        self.FUTURE_TOLERANCE_DAYS = 0
        # Priorité à la variable d'environnement/setting Scrapy MAX_PAGES (ex: -s MAX_PAGES=2)
        # 0 ou valeur manquante = illimité, sinon arrêter après N pages
        env_max = os.getenv('MAX_PAGES')
        if env_max is not None:
            try:
                self.MAX_PAGES = int(env_max)
            except Exception:
                self.MAX_PAGES = self.config.get('max_pages', 0)
        else:
            self.MAX_PAGES = self.config.get('max_pages', 0)

        # Compteur de pages effectivement traitées lors de ce run
        self.pages_processed = 0
        # Compteur d'items émis et plafond optionnel
        self.items_emitted = 0
        try:
            self.MAX_ITEMS = int(os.getenv('MAX_ITEMS', '0'))
        except Exception:
            self.MAX_ITEMS = 0
        
        # Headers
        self.HEADERS = self.config['headers']
        
        # Allowed domains
        domain = self.config['base_url'].replace('https://', '').replace('http://', '').rstrip('/')
        self.allowed_domains = [domain]
        
        self.logger.info(f"Spider initialisé pour {self.config['name']} ({self.kanton_kurz})")
        if self.min_date:
            self.logger.info(f"Mode incrémental (--days): scraping depuis {self.min_date.isoformat()}")
        elif self.ab:
            self.logger.info(f"Mode incrémental (--ab): scraping depuis {self.ab}")
        else:
            self.logger.info("Mode initial: scraping complet")
            # Log sort mode
            if self.sort:
                self.logger.info(f"Sort mode: {self.sort}")

        # Anti-doublons par DocId (pour éviter les répétitions entre pages/réponses)
        self.seen_ids = set()
        # Diagnostics pagination
        self._prev_req_ints = None
        self._prev_docid_sample = None
        # Early-stop flag when sorted by publication date
        self._older_seen = False
        # Recent page minima for monotonicity check
        self._recent_page_mins = []
        # Track DocIds already yielded to avoid duplicate emission
        self._yielded_docids = set()

    # --- Helpers ---
    def _can_emit_more(self) -> bool:
        """Retourne True si l'on peut encore émettre des items (MAX_ITEMS non atteint)."""
        try:
            mi = int(self.MAX_ITEMS)
        except Exception:
            mi = 0
        return (mi <= 0) or (self.items_emitted < mi)

    def _safe_get(self, tokens: List[str], idx: int, default: str = "") -> str:
        """Safe access into token lists without raising IndexError."""
        try:
            if tokens is None:
                return default
            if idx < 0:
                # support negative indices relative to end
                idx = len(tokens) + idx
            return tokens[idx] if 0 <= idx < len(tokens) else default
        except Exception:
            return default

    def _extract_values(self, gwt_text: str) -> List[str]:
        """Nettoie le préfixe GWT et extrait les valeurs sous forme de tokens."""
        content = RE_VOR.sub('', gwt_text)
        return RE_ALL.findall(content)

    def _find_docid_indexes(self, tokens: List[str]) -> List[int]:
        """Retourne les index des DocIds dans la liste de tokens."""
        out: List[int] = []
        for i, v in enumerate(tokens):
            if RE_ID.fullmatch(v or ''):
                out.append(i)
        return out

    def _slice_tokens(self, tokens: List[str], idx: int) -> List[str]:
        """Retourne une fenêtre locale autour d'un index pour absorber les décalages."""
        start = max(0, idx - ROW_WINDOW_BEFORE)
        end = min(len(tokens), idx + ROW_WINDOW_AFTER)
        return tokens[start:end]

    def _extract_row_meta(self, tokens: List[str], counters: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Union[str, bool]]]:
        """Extract lightweight metadata from a row window without issuing network requests.

        Returns a dict with keys: 'DocId','Num','EDatum','PDatum','Pfad','NeuePfadsyntax' or None if no DocId found.
        This allows deciding whether to defer expensive decrypt requests.
        """
        # find doc id index within window
        id_pos = None
        for i, v in enumerate(tokens):
            if self.reID.fullmatch(v or ''):
                id_pos = i
                break
        if id_pos is None:
            if isinstance(counters, dict):
                counters['id_missing'] = counters.get('id_missing', 0) + 1
            return None

        doc_id = tokens[id_pos]
        doc_id = self._safe_get(tokens, id_pos, '')
        num = self._detect_num(tokens, id_pos, counters)
        pfad, neue = self._detect_pdf_path(tokens, counters)
        edatum, pdatum = self._detect_dates(tokens, id_pos, neue, counters)

        return {
            'DocId': doc_id,
            'Num': num,
            'EDatum': edatum,
            'PDatum': pdatum,
            'Pfad': pfad,
            'NeuePfadsyntax': neue,
        }

    def _detect_num(self, tokens: List[str], id_pos: int, counters: Optional[Dict[str, int]]) -> str:
        """Détecte un numéro de dossier adjacent à l'ID, sinon scanne toute la fenêtre."""
        base_num_idx = id_pos + 2
        candidates_num = [base_num_idx, base_num_idx + 1, base_num_idx - 1]
        num: Optional[str] = None
        for ni in candidates_num:
            if 0 <= ni < len(tokens) and (RE_NUM1.fullmatch(tokens[ni] or '') or RE_NUM2.fullmatch(tokens[ni] or '')):
                num = tokens[ni]
                break
        if not num:
            for ni in range(0, len(tokens)):
                if RE_NUM1.fullmatch(tokens[ni] or '') or RE_NUM2.fullmatch(tokens[ni] or ''):
                    num = tokens[ni]
                    break
        if not num:
            if counters is not None and isinstance(counters, dict):
                counters['num_missing'] = counters.get('num_missing', 0) + 1
            num = ""
        return num or ""

    def _detect_pdf_path(self, tokens: List[str], counters: Optional[Dict[str, int]]) -> Tuple[Optional[str], bool]:
        """Détecte le chemin PDF (classique ou chiffré) dans la fenêtre locale."""
        neuePfadsyntax = False
        pfad: Optional[str] = None
        for idx in range(0, len(tokens)):
            if RE_PFAD.fullmatch(tokens[idx] or ''):
                pfad = tokens[idx]
                break
            elif RE_PFAD2.fullmatch(tokens[idx] or ''):
                pfad = tokens[idx]
                neuePfadsyntax = True
                break
        if not pfad and isinstance(counters, dict):
            counters['pfad_missing'] = counters.get('pfad_missing', 0) + 1
        return pfad, neuePfadsyntax

    def _detect_dates(self, tokens: List[str], id_pos: int, neuePfadsyntax: bool, counters: Optional[Dict[str, int]]) -> Tuple[str, str]:
        """Détecte la date de décision (EDatum) et la date de publication (PDatum)."""
        entscheiddatum: str = ""
        publikationsdatum: str = ""
        base_date_idx = id_pos + 3
        # Compute a future cutoff early (tolerance fixed to 0 days)
        try:
            today = datetime.utcnow().date()
            future_cutoff = today + timedelta(days=0)
        except Exception:
            future_cutoff = None
        if base_date_idx < len(tokens) and RE_DATUM.fullmatch(tokens[base_date_idx]):
            entscheiddatum = tokens[base_date_idx]
        elif base_date_idx + 1 < len(tokens) and RE_DATUM.fullmatch(tokens[base_date_idx + 1]):
            entscheiddatum = tokens[base_date_idx + 1]
        else:
            for off in range(0, min(20, len(tokens))):
                idxd = base_date_idx + off
                if idxd < len(tokens) and RE_DATUM.fullmatch(tokens[idxd]):
                    entscheiddatum = tokens[idxd]
                    break
        # If an EDatum was found, filter out obviously-future dates according to configured tolerance.
        if entscheiddatum:
            try:
                ed_d = datetime.strptime(entscheiddatum, '%Y-%m-%d').date()
            except Exception:
                ed_d = None
            try:
                if ed_d is not None and future_cutoff is not None and ed_d > future_cutoff:
                    # Treat future EDatum as missing for cutoff/heuristics purposes
                    entscheiddatum = ''
                    if isinstance(counters, dict):
                        counters['edatum_missing'] = counters.get('edatum_missing', 0) + 1
            except Exception:
                pass
        else:
            if isinstance(counters, dict):
                counters['edatum_missing'] = counters.get('edatum_missing', 0) + 1

        # Publication date: often near the end; adjust length if neuePfadsyntax
        l = len(tokens)
        if neuePfadsyntax and l > 22:
            l = l - 8
        # Collect potential publication dates by heuristics
        candidates: List[str] = []
        for ti in [l - 1, l - 2, l - 3]:
            if 0 <= ti < len(tokens) and RE_DATUM.fullmatch(tokens[ti] or ''):
                candidates.append(tokens[ti])
        if not candidates:
            start_pub_idx = base_date_idx + 2
            for off in range(0, 8):
                idxp = start_pub_idx + off
                if idxp < len(tokens) and RE_DATUM.fullmatch(tokens[idxp] or ''):
                    candidates.append(tokens[idxp])
        if not candidates:
            for idxp in range(0, len(tokens)):
                if RE_DATUM.fullmatch(tokens[idxp] or '') and (not entscheiddatum or tokens[idxp] != entscheiddatum):
                    candidates.append(tokens[idxp])
        # Refine selection: choose latest plausible date (<= today+1y), prefer non-equal to EDatum
        # future_cutoff already computed above (tolerance fixed to 0)
        chosen: Optional[str] = None
        if candidates:
            # Unique and filter
            uniq = []
            for c in candidates:
                if c not in uniq:
                    uniq.append(c)
            # Convert to dates and filter out obvious future values
            scored = []
            for c in uniq:
                try:
                    d = datetime.strptime(c, '%Y-%m-%d').date()
                except Exception:
                    d = None
                if d is None:
                    continue
                if future_cutoff and d > future_cutoff:
                    continue
                # Prefer not equal to EDatum
                eq_e = (entscheiddatum == c) if entscheiddatum else False
                scored.append((d, c, eq_e))
            if scored:
                # Sort by date desc, prefer non-equal to EDatum
                scored.sort(key=lambda x: (x[0], not x[2]), reverse=True)
                chosen = scored[0][1]
                # Debug: log selection when multiple
                if len(scored) > 1:
                    try:
                        self.logger.debug(f"PDatum candidates: {[c for _,c,_ in scored]} chosen={chosen}")
                    except Exception:
                        pass
        publikationsdatum = chosen or (candidates[0] if candidates else '')
        return entscheiddatum or "", publikationsdatum or ""

    def _build_pdf_candidates_from_decrypt(self, response_text: str, item: dict) -> Optional[Tuple[str, List[str]]]:
        """Construit les URLs candidates PDF à partir du texte de décryptage.

        Retourne (first_url, other_candidates) ou None si insuffisant.
        """
        import re as _re
        pm = _re.search(r'"partURL"\s*,\s*"([^"\\]+)"', response_text)
        part = pm.group(1) if pm else None
        if not part:
            return None

        # dossiernummer from response or item
        dossier_from_item = (item.get('Num') or '').replace(' ', '_')
        dm = _re.search(r'"dossiernummer"\s*,\s*"([^"\\]+)"', response_text)
        dossier = dm.group(1) if dm else dossier_from_item
        try:
            hash_token = part.rsplit('_', 1)[-1]
        except Exception:
            hash_token = part

        from urllib.parse import urlencode, quote
        qs = urlencode({'path': hash_token, 'pathIsEncrypted': '1', 'dossiernummer': dossier})
        base_dl = self.DOWNLOAD_URL.rstrip('/')
        servlet_candidates = [
            f"{base_dl}/tribunavtplus/ServletDownload/{dossier}_{hash_token}?{qs}",
            f"{base_dl}/tribunavtplus/ServletDownload/{dossier}_{hash_token}.pdf?{qs}",
        ]

        base = self.config.get('base_url', '').rstrip('/') or self.DOWNLOAD_URL.rstrip('/')
        candidates: List[str] = list(servlet_candidates)

        # Expand part-based path patterns
        try:
            parts = part.split('_')
        except Exception:
            parts = []
        if len(parts) >= 4:
            candidates.extend([
                f"{base}/tribunavtplus/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
                f"{base}/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
                f"{self.DOWNLOAD_URL.rstrip('/')}/tribunavtplus/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
                f"{self.DOWNLOAD_URL.rstrip('/')}/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
            ])

        part_enc = quote(part, safe='')
        candidates.extend([
            f"{base}/tribunavtplus/download?partURL={part}",
            f"{base}/tribunavtplus/download?partURL={part_enc}",
            f"{base}/tribunavtplus/pdf?partURL={part}",
            f"{base}/tribunavtplus/pdf?partURL={part_enc}",
            f"{base}/tribunavtplus/document?partURL={part}",
            f"{base}/tribunavtplus/document?partURL={part_enc}",
            f"{base}/tribunavtplus/getFile?partURL={part}",
            f"{base}/tribunavtplus/getFile?partURL={part_enc}",
            f"{base}/tribunavtplus/serve?partURL={part}",
            f"{base}/tribunavtplus/serve?partURL={part_enc}",
            f"{base}/tribunavtplus/publikation?download=true&partURL={part}",
            f"{base}/tribunavtplus/publikation?download=true&partURL={part_enc}",
            f"{base}/{part}",
        ])

        first = candidates.pop(0)
        return first, candidates

    def _is_pdf_ok(self, response: Response) -> bool:
        """Renvoie True si la réponse contient un PDF (status 200/206 + content-type/application/pdf)."""
        try:
            status = getattr(response, 'status', None)
            if status not in (200, 206):
                return False
            ct = response.headers.get('Content-Type') or response.headers.get(b'Content-Type')
            if isinstance(ct, (list, tuple)):
                ct = ct[0]
            if isinstance(ct, bytes):
                ct = ct.decode('utf-8', errors='ignore')
            ct_str = (ct or '').lower()
            return ('pdf' in ct_str) or str(response.url).lower().endswith('.pdf')
        except Exception:
            return False

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        """Instancie le spider et récupère tôt les settings (MAX_PAGES, etc.)."""
        spider = super(TribunaSpider, cls).from_crawler(crawler, *args, **kwargs)
        try:
            max_from_settings = crawler.settings.getint('MAX_PAGES', 0)
        except Exception:
            max_from_settings = 0
        if max_from_settings:
            spider.MAX_PAGES = max_from_settings
        # Charger MAX_ITEMS depuis les settings si fourni via -s MAX_ITEMS=..
        try:
            max_items = crawler.settings.getint('MAX_ITEMS', 0)
        except Exception:
            max_items = 0
        if max_items:
            spider.MAX_ITEMS = max_items
        return spider
    
    def start_requests(self):
        """Génère et yield la première requête POST vers RESULT_PAGE_URL."""
        # Si Scrapy a reçu un setting MAX_PAGES via -s, l'utiliser en priorité.
        try:
            if hasattr(self, 'crawler') and self.crawler and self.crawler.settings:
                s_val = self.crawler.settings.getint('MAX_PAGES', fallback=None)
                if s_val:
                    self.MAX_PAGES = s_val
        except Exception:
            pass

        body = self.get_next_request()

        # la page courante (après get_next_request) est self.page_nr - 1
        current_page = self.page_nr - 1

        if self.COOKIE:
            # Initialiser le cookie d'abord
            orequest = scrapy.Request(
                url=self.RESULT_PAGE_URL,
                method="POST",
                body=body,
                headers=self.HEADERS,
                callback=self.parse_page,
                meta={'page_nr': current_page},
                errback=self.errback_httpbin,
                dont_filter=True
            )
            request = scrapy.Request(
                url=self.COOKIE_INIT,
                headers=self.HEADERS,
                callback=self.set_cookie,
                errback=self.errback_httpbin,
                meta={'request': orequest, 'referrer_policy': "no-referrer", 'page_nr': current_page},
                dont_filter=True
            )
            yield request
        else:
            # Requête directe sans cookie
            yield scrapy.Request(
                url=self.RESULT_PAGE_URL,
                method="POST",
                body=body,
                headers=self.HEADERS,
                callback=self.parse_page,
                errback=self.errback_httpbin,
                meta={'referrer_policy': "no-referrer", 'page_nr': current_page},
                dont_filter=True
            )
    
    def get_next_request(self):
        """Génère le body de la prochaine requête GWT (pagination numérique)."""
        self.logger.info(f"Génération de la requête pour la page {self.page_nr}")
        millis = str(int(time.time() * 1000))
        # Determine sort token if available in config
        sort_token = None
        sort_key_used = None
        try:
            tokens = self.config.get('sort_tokens')
            if isinstance(tokens, dict) and self.sort and self.sort in tokens:
                sort_token = tokens[self.sort]
                sort_key_used = self.sort
            elif isinstance(tokens, dict) and self.sort == 'publication' and 'date_publication' in tokens:
                sort_token = tokens['date_publication']
                sort_key_used = 'date_publication'
        except Exception:
            sort_token = None

        if self.min_date is not None:
            # Utiliser le template avec date si disponible; sinon fallback sur RESULT_QUERY_TPL et filtrer côté parse
            datum = self.min_date.isoformat()
            # Prefer a sort-aware template if provided
            if sort_token and 'result_query_tpl_ab_sort' in self.config:
                body = self.config['result_query_tpl_ab_sort'].format(page_nr=self.page_nr, millis=millis, datum=datum, sort_token=sort_token)
                self.logger.info(f"Template: result_query_tpl_ab_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL_AB.format(page_nr=self.page_nr, millis=millis, datum=datum)
                self.logger.info(f"Template: result_query_tpl_ab; sort=None; page={self.page_nr}")
        elif self.ab is None:
            # If a dedicated UI-captured body is provided for publication sort, prefer it when no date filter is used
            ui_pub_sort = self.config.get('ui_publication_sort_body')
            if sort_key_used == 'date_publication' and ui_pub_sort and self.min_date is None:
                try:
                    body = ui_pub_sort.format(page_nr=self.page_nr, millis=millis)
                except Exception:
                    body = ui_pub_sort
                self.logger.info(f"Template: ui_publication_sort_body; sort={sort_key_used}; page={self.page_nr}")
            elif sort_token and 'result_query_tpl_sort' in self.config:
                body = self.config['result_query_tpl_sort'].format(page_nr=self.page_nr, millis=millis, sort_token=sort_token)
                self.logger.info(f"Template: result_query_tpl_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL.format(page_nr=self.page_nr, millis=millis)
                self.logger.info(f"Template: result_query_tpl; sort=None; page={self.page_nr}")
        else:
            if sort_token and 'result_query_tpl_ab_sort' in self.config:
                body = self.config['result_query_tpl_ab_sort'].format(page_nr=self.page_nr, millis=millis, datum=self.ab, sort_token=sort_token)
                self.logger.info(f"Template: result_query_tpl_ab_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL_AB.format(page_nr=self.page_nr, millis=millis, datum=self.ab)
                self.logger.info(f"Template: result_query_tpl_ab; sort=None; page={self.page_nr}")
        
        self.page_nr += 1
        return body
    
    def set_cookie(self, response):
        """Gère l'initialisation du cookie si nécessaire"""
        self.logger.info(f"Initialisation du cookie pour {self.COOKIE_INIT}")
        
        if response.status == 200:
            self.logger.info(f"Headers reçus: {response.headers}")
            # Collecter tous les Set-Cookie
            raw_cookies = response.headers.getlist(b'Set-Cookie') or response.headers.getlist('Set-Cookie')
            cookie_parts = []
            for rc in raw_cookies:
                try:
                    s = rc.decode('utf-8', errors='ignore') if isinstance(rc, bytes) else str(rc)
                    name_value = s.split(';', 1)[0].strip()
                    if name_value:
                        cookie_parts.append(name_value)
                except Exception as e:
                    self.logger.debug(f"Erreur décodage Set-Cookie: {e}")
            if cookie_parts:
                cookie_header = '; '.join(cookie_parts)
                request = response.meta['request']
                # Assigner en tant que chaîne (Scrapy gère l'encodage)
                request.headers['Cookie'] = cookie_header
                self.logger.info("Cookie défini, lancement requête avec cookie")
                yield request
            else:
                self.logger.warning("Aucun cookie valide reçu, tentative sans cookie")
                yield response.meta['request']
        else:
            self.logger.error(f"Erreur initialisation cookie: status {response.status}")
    
    def parse_page(self, response):
        """Parse une page de résultats GWT et émet items + requête de page suivante."""
        # Récupérer le numéro de page transmis via meta (fiable).
        current_page = response.meta.get('page_nr', self.page_nr - 1)
        self.logger.info(f"Parsing page {current_page}")
        # Incrémenter le nombre de pages réellement traitées
        try:
            self.pages_processed += 1
        except Exception:
            self.pages_processed = 1
        # Scrapy stats
        try:
            self.crawler.stats.inc_value('tribuna/pages_processed')
        except Exception:
            pass
        # Log complet du body pour les premières pages et comparer les champs numériques
        try:
            body_str = response.request.body.decode('utf-8', errors='ignore')
        except Exception:
            body_str = ''
        if current_page <= 2:
            self.logger.debug(f"Requête complète (page {current_page}): {body_str}")
        # Extraire et logguer l'index de page du payload (slot |20|x|-1|) pour INFO
        try:
            m_page = re.search(r"\|20\|(-?\d+)\|\-1\|", body_str)
            if m_page:
                self.logger.info(f"Payload page slot (|20|x|-1|): {m_page.group(1)}")
        except Exception:
            pass
        # Extraire les entiers du body et comparer à la page précédente pour vérifier la pagination
        try:
            ints = [int(m) for m in re.findall(r'\|(-?\d+)\|', body_str)]
        except Exception:
            ints = []
        if self._prev_req_ints is not None and ints:
            try:
                diff_idx = [i for i,(a,b) in enumerate(zip(self._prev_req_ints, ints)) if a != b]
                if diff_idx:
                    self.logger.debug(f"Champs numériques modifiés aux index: {diff_idx[:10]}")
                else:
                    self.logger.debug("Aucun champ numérique du body n'a changé vs page précédente")
            except Exception:
                pass
        if ints:
            self._prev_req_ints = ints
        
        if response.status == 200 and len(response.body) >= MINIMUM_PAGE_LEN:
            # Extraire le nombre total de résultats
            # Utiliser current_page (0-based) ou si trefferzahl encore inconnu
            if current_page == 0 or self.trefferzahl == 0:
                treffer = RE_TREFFER.search(response.text)
                if treffer:
                    self.trefferzahl = int(treffer.group())
                    self.logger.info(f"Nombre total de résultats: {self.trefferzahl}")
            
            if self.trefferzahl > 0:
                # Nettoyer la réponse GWT
                content = RE_VOR.sub('', response.text)
                self.logger.debug(f"Contenu nettoyé: {content[:500]}")
                
                # Extraire les valeurs avec regex
                werte = RE_ALL.findall(content)

                if len(werte) < 9:
                    self.logger.warning(f"Résultats insuffisants ({len(werte)} valeurs), page ignorée")
                else:
                    # Nouvelle logique: traiter plusieurs lignes par page en se basant sur les positions des DocId
                    yielded_this_page = 0
                    page_pdates = []
                    id_indexes = self._find_docid_indexes(werte)

                    if not id_indexes:
                        self.logger.warning("Aucun DocId détecté sur cette page")
                    else:
                        self.logger.debug(f"DocIds détectés: {len(id_indexes)} (premiers index: {id_indexes[:5]})")

                    # Par heuristique, une page UI affiche ~20 lignes; on limite à 25 pour ne pas rater
                    # des lignes décalées. On log le nombre d'IDs détectés et le nombre d'items yieldés.
                    # Sample first few DocIds for troubleshooting
                    sample_ids = [self._safe_get(werte, i, '') for i in id_indexes[:5]] if id_indexes else []
                    self.logger.info(f"Page {current_page}: {len(id_indexes)} DocIds détectés (exemples: {sample_ids})")
                    # Noter si l'échantillon semble identique à la page précédente (diagnostic, pas d'arrêt ici)
                    try:
                        if self._prev_docid_sample is not None and sample_ids and self._prev_docid_sample == sample_ids:
                            self.logger.warning("Échantillon DocIds identique à la page précédente — possible répétition de page")
                            similar_to_prev = True
                        else:
                            similar_to_prev = False
                    except Exception:
                        similar_to_prev = False

                    # Counters for drop reasons on this page
                    drop_counters = {
                        'id_missing': 0,
                        'num_missing': 0,
                        'pfad_missing': 0,
                        'decrypt_failed': 0,
                        'edatum_missing': 0,
                        'old_item': 0,
                        'dedup_skipped': 0,
                    }

                    # Per-page date counters (for page-fraction early-stop)
                    page_count_with_dates = 0
                    page_count_older = 0
                    # Respecter un plafond global d'items si demandé
                    for idx in id_indexes[:25]:
                        if not self._can_emit_more():
                            self.logger.info(f"Cap MAX_ITEMS atteint ({self.MAX_ITEMS}), arrêt de l'émission sur cette page")
                            break
                        try:
                            doc_id = self._safe_get(werte, idx, None)
                        except Exception:
                            continue

                        # Déduplication globale par DocId
                        if not doc_id or doc_id in self.seen_ids:
                            drop_counters['dedup_skipped'] += 1
                            continue
                        self.seen_ids.add(doc_id)

                        # Construire une vue locale plus large autour de l'ID pour absorber les décalages
                        # Certains champs (leitsatz avec highlights) décalent les positions; élargir la fenêtre
                        # élargir la fenêtre locale pour absorber des décalages importants
                        # increase window to capture displaced tokens and long highlights
                        slice_werte = self._slice_tokens(werte, idx)

                        # Lightweight metadata extraction to decide whether to decrypt
                        meta = self._extract_row_meta(slice_werte, drop_counters)
                        if meta is None:
                            continue

                        # Prefer publication date, fallback to decision date for range checks
                        pdate_str = meta.get('PDatum') or meta.get('EDatum') or ''
                        pdate_obj = None
                        if pdate_str:
                            try:
                                pdate_obj = datetime.strptime(pdate_str, '%Y-%m-%d').date()
                            except Exception:
                                pdate_obj = None
                        if pdate_obj:
                            page_count_with_dates += 1
                            page_pdates.append(pdate_obj)

                        # If configured to defer decrypts, skip rows older than min_date early
                        if self.DEFER_DECRYPT and self.min_date and pdate_obj and pdate_obj < self.min_date:
                            drop_counters['old_item'] = drop_counters.get('old_item', 0) + 1
                            page_count_older += 1
                            # Do not issue decrypt for this row; skip emitting
                            continue

                        # Otherwise perform full parsing (which may issue decrypt request)
                        parsed = self.parse_result_row(slice_werte, content, page_nr=current_page, counters=drop_counters)

                        # parse_result_row peut retourner: None | Request | Item | Iterable
                        if hasattr(parsed, '__iter__') and not isinstance(parsed, (dict, scrapy.Request)):
                            for r in parsed:
                                if not self._can_emit_more():
                                    continue
                                if isinstance(r, dict):
                                    docid = r.get('DocId')
                                    if docid and docid in self._yielded_docids:
                                        continue
                                    if docid:
                                        self._yielded_docids.add(docid)
                                    self.items_emitted += 1
                                    try:
                                        self.crawler.stats.inc_value('tribuna/items_emitted')
                                    except Exception:
                                        pass
                                yielded_this_page += 1
                                yield r
                        elif parsed:
                            if not self._can_emit_more():
                                pass
                            if isinstance(parsed, dict):
                                docid = parsed.get('DocId')
                                if docid and docid in self._yielded_docids:
                                    pass
                                else:
                                    if docid:
                                        self._yielded_docids.add(docid)
                                    # collect PDatum if available
                                    try:
                                        p = parsed.get('PDatum')
                                        if p:
                                            try:
                                                pd = datetime.strptime(p, '%Y-%m-%d').date()
                                                page_pdates.append(pd)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                    self.items_emitted += 1
                                    try:
                                        self.crawler.stats.inc_value('tribuna/items_emitted')
                                    except Exception:
                                        pass
                                    yielded_this_page += 1
                                    yield parsed

                    if yielded_this_page == 0:
                        self.logger.info("Aucune ligne exploitable trouvée sur la page (après déduplication)")
                    else:
                        self.logger.info(f"Page {current_page}: {yielded_this_page} items yieldés")

                    # Log drop reasons for this page
                    # Push drop counters into Scrapy stats
                    try:
                        for k, v in drop_counters.items():
                            if v:
                                self.crawler.stats.inc_value(f'tribuna/drops/{k}', v)
                    except Exception:
                        pass
                    self.logger.info(f"Page {current_page} drops: id_missing={drop_counters['id_missing']}, num_missing={drop_counters['num_missing']}, pfad_missing={drop_counters['pfad_missing']}, decrypt_failed={drop_counters['decrypt_failed']}, edatum_missing={drop_counters['edatum_missing']}, old_item={drop_counters.get('old_item', 0)}, dedup_skipped={drop_counters.get('dedup_skipped', 0)}")
                    # Diagnostics: min/max PDatum sur la page
                    try:
                        if page_pdates:
                            page_min = min(page_pdates)
                            page_max = max(page_pdates)
                            samples = [d.isoformat() for d in sorted(page_pdates, reverse=True)[:3]]
                            self.logger.info(f"Page {current_page} PDatum range: min={page_min.isoformat()} max={page_max.isoformat()} samples={samples}")
                            # Record recent minima for monotonicity check (first pages)
                            try:
                                if len(self._recent_page_mins) >= 3:
                                    self._recent_page_mins.pop(0)
                                self._recent_page_mins.append(page_min)
                                if len(self._recent_page_mins) >= 2:
                                    # If monotonic non-increasing violated, warn
                                    if any(self._recent_page_mins[i] < self._recent_page_mins[i+1] for i in range(len(self._recent_page_mins)-1)):
                                        self.logger.warning("Server-side publication sort does not appear monotonic across sampled pages")
                            except Exception:
                                pass
                            # Page-fraction early-stop when sorted by publication: require a threshold fraction of dated items to be older than min_date
                            if getattr(self, 'sort', None) == 'publication' and self.min_date and page_count_with_dates > 0:
                                try:
                                    frac_old = float(page_count_older) / float(page_count_with_dates)
                                except Exception:
                                    frac_old = 0.0
                                self.logger.info(f"Page {current_page} older fraction: {frac_old:.2f} (threshold={self.PAGE_OLDER_FRACTION:.2f})")
                                if frac_old >= float(self.PAGE_OLDER_FRACTION):
                                    self._older_seen = True
                                # If user requested an incremental run (--days), be strict: if any dated item on this page
                                # is older than the cutoff, stop pagination immediately when sorted by publication.
                                if getattr(self, 'days', None) is not None and page_count_older > 0:
                                    self.logger.info("Arrêt immédiat: élément plus ancien que le cutoff trouvé sur la page (mode --days)")
                                    self._older_seen = True
                    except Exception:
                        pass
                    # Mémoriser l'échantillon DocIds courant pour comparaison avec la page suivante
                    try:
                        self._prev_docid_sample = list(sample_ids) if sample_ids else None
                    except Exception:
                        self._prev_docid_sample = None
            else:
                self.logger.warning("0 résultats trouvés")
            
            # Pagination: arrêter si min_date atteinte et plus d'items valides sur cette page (heuristique)
            stop_due_to_date = False
            if self.min_date:
                # Heuristique: si on a traité des items et tous (ou la majorité) ont PDatum < min_date, arrêter
                # Comme on ne stocke pas tous ici, on arrête si la page suivante dépasse MAX_PAGES ou si page_nr déjà suffisamment élevé
                # Simplification: on continue tant que page_nr < MAX_PAGES
                stop_due_to_date = False

            # Pagination
            # Si MAX_PAGES > 0, stopper strictement après N pages TRAITÉES, peu importe le numéro logique de page
            should_continue = True
            if isinstance(self.MAX_PAGES, int) and self.MAX_PAGES > 0:
                should_continue = (self.pages_processed < self.MAX_PAGES)

            # Arrêter la pagination si le cap d'items est atteint
            if not self._can_emit_more():
                should_continue = False
                try:
                    # Fermer proprement le spider via l'engine Scrapy
                    if hasattr(self, 'crawler') and self.crawler and self.crawler.engine:
                        self.crawler.engine.close_spider(self, reason='max_items_reached')
                except Exception:
                    pass

            # Arrêt anticipé si l'échantillon DocIds n'a pas changé par rapport à la page précédente
            # Note: la comparaison est faite plus haut et stockée dans similar_to_prev
            try:
                if should_continue and 'similar_to_prev' in locals() and similar_to_prev and current_page > 0:
                    self.logger.warning("Arrêt pagination: pages consécutives semblent identiques (échantillon DocIds)")
                    should_continue = False
            except Exception:
                pass

            # Early-stop when sorted by publication date and older items seen
            try:
                if should_continue and getattr(self, 'sort', None) == 'publication' and self._older_seen:
                    self.logger.info("Arrêt pagination: items plus anciens que min_date rencontrés sous tri publication")
                    should_continue = False
            except Exception:
                pass

            if not stop_due_to_date and should_continue:
                body = self.get_next_request()
                # nouveau numéro de page pour la requête suivante
                next_page = self.page_nr - 1
                yield scrapy.Request(
                    url=self.RESULT_PAGE_URL,
                    method="POST",
                    body=body,
                    headers=self.HEADERS,
                    callback=self.parse_page,
                    errback=self.errback_httpbin,
                    meta={'page_nr': next_page},
                    dont_filter=True
                )
            else:
                self.logger.info(f"Fin du scraping: {self.pages_processed} pages traitées (MAX_PAGES={self.MAX_PAGES})")
        else:
            self.logger.error(f"Réponse invalide: status={response.status}, len={len(response.body)}")
    
    def parse_result_row(self, werte, content, page_nr=None, counters=None):
        """
        Parse une ligne de résultat GWT
        
        Args:
            werte: Liste des valeurs extraites par regex
            content: Contenu brut pour debug
            
        Returns:
            PublicationItem ou None si parsing échoué
        """
        try:
            korrektur = 0
            brauchbar = True

            # Extraire l'ID dynamiquement dans la fenêtre
            id_pos = None
            # Rechercher l'ID dans toute la fenêtre locale
            for i in range(len(werte)):
                v = self._safe_get(werte, i, '')
                if self.reID.fullmatch(v or ''):
                    id_pos = i
                    break
            if id_pos is None:
                # increment id_missing counter if provided
                if counters is not None and isinstance(counters, dict):
                    counters['id_missing'] += 1
                self.logger.debug(f"ID non trouvé dans fenêtre locale")
                return None
            id_ = self._safe_get(werte, id_pos, '')

            # Kammer virtuelle (si présente juste avant l'ID)
            vkammer = ""
            prev_tok = self._safe_get(werte, id_pos - 1, '')
            if prev_tok and len(prev_tok) > 8 and not self.reDatum.fullmatch(prev_tok):
                vkammer = prev_tok

            # Titre à l'index suivant l'ID
            titel_idx = id_pos + 1
            titel = self._safe_get(werte, titel_idx, '').replace("\\x27", "'")
            if len(titel) < 8:
                self.logger.warning(f"Titre trop court: '{titel}'")
                titel = ""

            # Numéro de dossier
            num = self._detect_num(werte, id_pos, counters)

            # Index de base pour champs après la date décision
            base_date_idx = id_pos + 3

            # Leitsatz généralement après la date
            leitsatz_idx = base_date_idx + 1
            leitsatz = self._safe_get(werte, leitsatz_idx, '').replace("\\x27", "'")
            if len(leitsatz) < 11 or leitsatz == '-':
                leitsatz = ""

            # Pfad PDF
            pfad, neuePfadsyntax = self._detect_pdf_path(werte, counters)

            # Dates EDatum et PDatum
            entscheiddatum, publikationsdatum = self._detect_dates(werte, id_pos, neuePfadsyntax, counters)
            
            # Rechtsgebiet
            rechtsgebiet = ""
            for ri in [leitsatz_idx + 1, leitsatz_idx + 2]:
                tok = self._safe_get(werte, ri, '')
                if RE_RG.fullmatch(tok):
                    rechtsgebiet = tok
                    break
            
            # Créer l'item
            numstr = num.replace(" ", "_")
            
            item = PublicationItem()
            item['Kanton'] = self.kanton_kurz
            item['DocId'] = id_
            item['Num'] = num
            item['Titel'] = titel
            item['Leitsatz'] = leitsatz
            # Normalize invalid zero-dates
            if isinstance(entscheiddatum, str) and entscheiddatum == '0000-00-00':
                entscheiddatum = ""
            if isinstance(publikationsdatum, str) and publikationsdatum == '0000-00-00':
                publikationsdatum = ""

            item['EDatum'] = entscheiddatum
            item['PDatum'] = publikationsdatum
            item['Rechtsgebiet'] = rechtsgebiet
            item['VKammer'] = vkammer
            item['Raw'] = content
            # Assigner le numéro de page (page_nr provient de response.meta).
            # Si le meta fournit 0 ou None (valeur par défaut), utiliser
            # le compteur de pages traitées `self.pages_processed` (1-based).
            if page_nr is not None:
                try:
                    item['PageNr'] = int(page_nr)
                except Exception:
                    # Fallback: approx zero-based from pages_processed
                    item['PageNr'] = max(0, int(self.pages_processed) - 1)
            else:
                # Fallback si meta absent: approx zero-based à partir de pages_processed
                item['PageNr'] = max(0, int(self.pages_processed) - 1)
            
            # Gestion du PDF (avec décryptage si nécessaire)
            if self.ENCRYPTED:
                # Préparer le pfad pour décryptage
                if not pfad:
                    # Impossible de décrypter sans pfad; retourner l'item sans PDF
                    item['PDFUrls'] = []
                    return item
                if neuePfadsyntax:
                    pfad_encrypt = f"{numstr}_{pfad}|dossiernummer|{numstr}"
                elif self.ASCII_ENCRYPTED:
                    ascii_pfad = ''
                    for c in pfad:
                        ascii_pfad += '|' + str(ord(c))
                    pfad_encrypt = ascii_pfad.replace("|92|92", "|92")
                else:
                    pfad_encrypt = pfad
                
                # Créer une requête de décryptage
                body = self.DECRYPT_START + pfad_encrypt + self.DECRYPT_END
                
                yield scrapy.Request(
                    url=self.DECRYPT_PAGE_URL,
                    method="POST",
                    body=body,
                    headers=self.HEADERS,
                    callback=self.decrypt_path,
                    errback=self.errback_httpbin,
                    meta={"item": item, 'page_nr': item.get('PageNr'), 'counters': counters},
                    dont_filter=True
                )
                return None  # Item sera yielded après décryptage
            else:
                # PDF direct sans décryptage
                href = self.PDF_PATTERN.format(
                    self.DOWNLOAD_URL,
                    numstr,
                    id_,
                    self.PDF_PATH,
                    id_,
                    numstr
                )
                item['PDFUrls'] = [href]
                return item
                
        except Exception as e:
            self.logger.error(f"Erreur parsing résultat: {e}", exc_info=True)
            return None
    
    def decrypt_path(self, response):
        """Décrypte le chemin PDF et émet l'item (avec PDF) ou des candidats."""
        item = response.meta['item']
        page_nr = response.meta.get('page_nr', self.page_nr - 1)
        counters = response.meta.get('counters')

        self.logger.info(f"Décryptage PDF pour DocID {item['DocId']} (page {page_nr})")
        
        if response.status == 200:
            self.logger.debug(f"Réponse decrypt: {response.text[:200]}")
            # Dump brut de la réponse de décryptage pour analyse (facile à consulter)
            try:
                out_dir = Path(__file__).parent.parent / 'output' / 'debug_decrypts'
                out_dir.mkdir(parents=True, exist_ok=True)
                fname = out_dir / f"decrypt_{item['DocId']}.txt"
                with open(fname, 'w', encoding='utf-8') as fh:
                    fh.write(response.text)
                self.logger.info(f"Réponse decrypt sauvegardée: {fname}")
            except Exception as _e:
                self.logger.warning(f"Impossible de sauvegarder réponse decrypt: {_e}")
            
            # Construire les candidats PDF via helper
            built = self._build_pdf_candidates_from_decrypt(response.text, item)
            if built:
                first, candidates = built
                self.logger.debug(f"Premier candidat PDF: {first}")
                # include GWT headers and allow non-200 responses to be handled in callback
                hdrs = dict(self.HEADERS)
                hdrs.update({'Referer': self.HEADERS.get('Referer', '')})
                yield scrapy.Request(
                    url=first,
                    method="GET",
                    headers=hdrs,
                    callback=self.verify_pdf_candidate,
                    errback=self.errback_httpbin,
                    meta={"item": item, "candidates": candidates, 'handle_httpstatus_all': True, 'page_nr': page_nr},
                    dont_filter=True
                )
                return

            self.logger.error(f"Impossible de décrypter le PDF pour {item['DocId']}")
            # Count decrypt failure per-page if counters present
            if isinstance(counters, dict):
                try:
                    counters['decrypt_failed'] = counters.get('decrypt_failed', 0) + 1
                    try:
                        self.crawler.stats.inc_value('tribuna/decrypt_failed')
                    except Exception:
                        pass
                except Exception:
                    pass
            # Yield l'item sans PDF plutôt que de le perdre
            if not self._can_emit_more():
                return
            item['PDFUrls'] = []
            try:
                docid = item.get('DocId')
                if docid and docid in self._yielded_docids:
                    return
                if docid:
                    self._yielded_docids.add(docid)
                self.items_emitted += 1
                try:
                    self.crawler.stats.inc_value('tribuna/items_emitted')
                except Exception:
                    pass
            except Exception:
                pass
            yield item
        else:
            self.logger.error(f"Erreur décryptage: status={response.status}")
            if isinstance(counters, dict):
                try:
                    counters['decrypt_failed'] = counters.get('decrypt_failed', 0) + 1
                    try:
                        self.crawler.stats.inc_value('tribuna/decrypt_failed')
                    except Exception:
                        pass
                except Exception:
                    pass
            if not self._can_emit_more():
                return
            item['PDFUrls'] = []
            try:
                docid = item.get('DocId')
                if docid and docid in self._yielded_docids:
                    return
                if docid:
                    self._yielded_docids.add(docid)
                self.items_emitted += 1
            except Exception:
                pass
            yield item

    def verify_pdf_candidate(self, response):
        """Vérifie si la réponse est un PDF; sinon teste le candidat suivant."""
        item = response.meta.get('item')
        candidates = response.meta.get('candidates', [])
        page_nr = response.meta.get('page_nr', self.page_nr - 1)

        # Considérer OK via helper
        ct = None
        try:
            ct = response.headers.get('Content-Type') or response.headers.get(b'Content-Type')
            if isinstance(ct, bytes):
                ct = ct.decode('utf-8', errors='ignore')
        except Exception:
            ct = None

        ok = self._is_pdf_ok(response)

        if ok:
            # URL valide — retourner l'item
            if not self._can_emit_more():
                return
            # Prevent duplicate yields by DocId
            docid = item.get('DocId')
            if docid and docid in self._yielded_docids:
                return
            if docid:
                self._yielded_docids.add(docid)
            item['PDFUrls'] = [response.url]
            item['PageNr'] = page_nr
            self.logger.info(f"PDF trouvé: {response.url} (content-type={ct})")
            try:
                self.items_emitted += 1
                try:
                    self.crawler.stats.inc_value('tribuna/items_emitted')
                except Exception:
                    pass
            except Exception:
                pass
            yield item
            return

        # Sinon, essayer le candidat suivant s'il existe
        if candidates:
            next_url = candidates.pop(0)
            self.logger.debug(f"Candidat PDF suivant: {next_url}")
            hdrs = dict(self.HEADERS)
            hdrs.update({'Referer': self.HEADERS.get('Referer', '')})
            yield scrapy.Request(
                url=next_url,
                method="GET",
                headers=hdrs,
                callback=self.verify_pdf_candidate,
                errback=self.errback_httpbin,
                meta={"item": item, "candidates": candidates, 'handle_httpstatus_all': True, 'page_nr': page_nr},
                dont_filter=True
            )
            return

        # Aucun candidat valide
        self.logger.warning(f"Aucun endpoint PDF valide trouvé pour {item['DocId']}")
        # increment decrypt_failed if counters available
        counters = response.meta.get('counters')
        if isinstance(counters, dict):
            try:
                counters['decrypt_failed'] = counters.get('decrypt_failed', 0) + 1
                try:
                    self.crawler.stats.inc_value('tribuna/decrypt_failed')
                except Exception:
                    pass
            except Exception:
                pass
        if not self._can_emit_more():
            return
        # Prevent duplicate yields
        docid = item.get('DocId')
        if docid and docid in self._yielded_docids:
            return
        if docid:
            self._yielded_docids.add(docid)
        item['PDFUrls'] = []
        item['PageNr'] = page_nr
        try:
            self.items_emitted += 1
            try:
                self.crawler.stats.inc_value('tribuna/items_emitted')
            except Exception:
                pass
        except Exception:
            pass
        yield item
    
    def errback_httpbin(self, failure):
        """Gestion des erreurs HTTP"""
        self.logger.error(f"Erreur requête: {failure.value}")
