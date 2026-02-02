import scrapy
import re
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Iterable, Union, Tuple
from urllib.parse import urljoin
from scrapy.http import Response, Request
from scrapy.utils.log import failure_to_exc_info
from publication_scraper.items import PublicationItem
from publication_scraper.spiders import gwt_utils

logger = logging.getLogger("mycustomlogger")

"""Tribuna GWT-RPC spider (Scrapy).

Ce projet scrape des applications Tribuna qui exposent leurs résultats via GWT-RPC.

Idée générale (mental model):
1) Bootstrap (GET) : on visite une page HTML du site pour récupérer des tokens GWT
    dynamiques (X-GWT-Permutation / X-GWT-Module-Base). Ces tokens changent quand
    l'application est redéployée; les garder en dur casse le spider.
2) LoadTable (POST GWT-RPC) : on envoie un body très proche de celui capturé dans
    l'UI (per canton). La réponse est un payload GWT (prefix //OK[...]) que l'on
    tokenise.
3) Parsing : on détecte les DocIds dans les tokens, on isole une "fenêtre" locale
    autour de chaque DocId pour éviter de mélanger plusieurs lignes.
4) PDF : selon le canton, le lien PDF est direct (pfad visible) ou nécessite une
    requête de décryptage puis plusieurs URLs candidates à tester.
5) Barrière de pagination (option) : on n'envoie la requête page N+1 qu'une fois
    que tous les DocIds de la page N ont atteint un état terminal (PDF OK ou échec
    final, item yieldé). Ça évite de "courir" devant des downloads PDF lents.
"""

# --- Module constants & regexes (for clarity and reuse) ---
# GWT response cleanup and token extraction
RE_VOR = gwt_utils.RE_VOR
RE_ALL = gwt_utils.RE_ALL
RE_ID = re.compile(r'[0-9a-f]{32}|[0-9]{15,17}')
RE_DATUM = re.compile(r'\d{4}-\d{2}-\d{2}')
RE_DATUM_CH = re.compile(r'\d{2}\.\d{2}\.\d{4}')
RE_RG = re.compile(r'[^0-9\.:\-]{3}.{3,}')
# Centralized GWT-only regexes
RE_TREFFER = gwt_utils.RE_TREFFER
RE_DECRYPT = gwt_utils.RE_DECRYPT
RE_DECRYPT2 = gwt_utils.RE_DECRYPT2
RE_PFAD = gwt_utils.RE_PFAD
RE_PFAD2 = gwt_utils.RE_PFAD2
# Optional/legacy
RE_DECODE = gwt_utils.RE_DECODE

RE_NUM = re.compile(r'.+')

# Dossier number patterns
RE_NUM1 = re.compile(r'^\d{3,4}\s+\d{4}\s+\d+$')
RE_NUM2 = re.compile(r'^\d{3,4}_\d{4}_\d+$')

DEFAULT_NUM_PATTERN_STRINGS = [
    # Common Tribuna dossier format (e.g., "100 2024 331")
    r'^\d{1,4}\s+\d{4}\s+\d{1,6}$',
    r'^\d{1,4}_\d{4}_\d{1,6}$',
    r'[A-Z]{1,5}\s?\d{2,4}\s?\d{1,4}',
    r'[A-Z]{1,5}\s?\d{1,4}/\d{1,4}',
    r'\d{1,4}\s?[A-Z]{1,5}\s?\d{1,4}',
    r'[A-Z]{1,5}-\d{1,4}-\d{2,4}',
    r'[A-Z]{1,3}\s?\d{2,4}\.\d{2}',
]
DEFAULT_NUM_REGEXES = [re.compile(p) for p in DEFAULT_NUM_PATTERN_STRINGS]
NUM_SEARCH_RADIUS = 12
MAX_JOINED_TOKENS = 3

# Heuristics
ROW_WINDOW_BEFORE = 25
ROW_WINDOW_AFTER = 60
MAX_IDS_PER_PAGE = 25
MINIMUM_PAGE_LEN = 148  # Minimum response length to consider a page "valid" for parsing

class TribunaGraubundenSpider(scrapy.Spider):
    """
    Spider pour extraire les publications des sites Tribuna (Fribourg, Graubünden, etc.)
    
    Basé sur le modèle tribuna.py du client, utilise POST requests directes
    au lieu de Playwright pour parser les réponses GWT.
    
    Usage (CLI):
        # Scraping initial (toutes les décisions)
        scrapy crawl tribuna -a canton=fribourg

        # Scraping incrémental: X derniers jours (date_min = today - X, date_max = today)
        scrapy crawl tribuna -a canton=fribourg -a days=7

        # Mode validation/completude (plus strict sur la récupération des PDFs)
        scrapy crawl tribuna -a canton=fribourg -a days=7 -s STRICT_FULL=1
    """
    name = 'tribuna_graubunden'

    # Profil d'exécution par défaut (back-pressure doux en mode async)
    custom_settings = {
        'DOWNLOAD_DELAY': 1.0,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'AUTOTHROTTLE_ENABLED': True,
        'COOKIES_ENABLED': True,
    }

    # Regex pour parser les réponses GWT (inspiré de tribuna.py) - reuse module-level constants
    reVor = gwt_utils.RE_VOR
    reAll = gwt_utils.RE_ALL
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
    
    DEFAULT_CANTON = 'graubunden'

    def __init__(self, canton: Optional[str] = None, ab=None, days=None, sort=None, strict_full=False, *args, **kwargs):
        """Initialisation du spider.

        Entrées principales (CLI):
        - `canton=...` (obligatoire): clé dans `cantons_config.json`
        - `days=N` (recommandé): mode incrémental relatif ($[today-N, today]$)
        - `ab=YYYY-MM-DD` (legacy): mode incrémental “fixe” (compat)

        Paramètres “settings” (via `-s ...`):
        - `STRICT_FULL`: mode strict/completude (par défaut dans ce projet)
        - `MAX_PAGES`, `MAX_ITEMS`, `LOG_LEVEL`, etc.

                Note importante:
                - Le canton et `days` sont passés en arguments Scrapy (`-a`).
                - Les réglages runtime se font via les settings Scrapy (ex: `-s MAX_PAGES=...`).
        """
        super().__init__(*args, **kwargs)

        # Déterminer le canton par défaut si non fourni.
        if canton is None:
            canton = getattr(self, 'DEFAULT_CANTON', None)
        if not canton:
            raise ValueError("Missing canton: pass -a canton=... or set DEFAULT_CANTON on the spider")

        # Charger la configuration du canton
        config_path = Path(__file__).parent.parent / 'cantons_config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        
        if canton not in configs:
            raise ValueError(f"Canton '{canton}' non trouvé dans la configuration. Cantons disponibles: {list(configs.keys())}")
        
        self.config = configs[canton]
        self.canton = canton
        self.kanton_kurz = self.config['kanton_kurz']
        # --- Modes incrémentaux ---
        # Historiquement, on supportait `ab=YYYY-MM-DD` (date fixe). On le garde
        # pour compat, mais le mode recommandé est `days=N` car il exprime un
        # incrémental "relatif" (les N derniers jours) et évite les confusions.
        # Important: on ne lit pas `ab` depuis une source implicite (uniquement l'argument CLI)
        # pour éviter des overrides implicites.
        self.ab = None
        try:
            if ab is not None and str(ab).strip() != "":
                self.ab = str(ab).strip()
        except Exception:
            self.ab = None

        # Mode mise à jour: `days=N` => min_date=today-N, max_date=today.
        # Note: today est UTC pour éviter les effets de timezone.
        self.days = int(days) if days is not None else None
        self.min_date = None
        self.today = datetime.utcnow().date()
        if self.days is not None and self.days > 0:
            # today inclusive
            self.min_date = self.today - timedelta(days=self.days)

        # --- Tri & stratégie d'arrêt en days=N ---
        # Deux familles d'instances Tribuna existent:
        # A) Celles qui supportent le filtre UI "date start/end" côté serveur.
        #    Dans ce cas, le body loadTable contient typiquement un placeholder {datum}.
        #    => Le serveur renvoie déjà un sous-ensemble, donc on n'a pas besoin
        #       d'un tri spécial ni d'un stop basé sur la date.
        # B) Celles qui ne supportent pas le filtre serveur.
        #    => On demande/assume un tri par date de publication décroissante (si possible)
        #       et on stoppe quand on croise des éléments plus anciens que min_date.
        self.sort = sort
        # Server-side days filter capability (template contains {datum})
        self.supports_server_date_range = False
        if self.sort is None and self.days is not None and self.days > 0:
            tpl_ab = str(self.config.get('result_query_tpl_ab', '') or '')
            supports_server_date_range = '{datum}' in tpl_ab
            self.supports_server_date_range = bool(supports_server_date_range)
            self.sort = None if supports_server_date_range else 'publication'
        else:
            try:
                tpl_ab = str(self.config.get('result_query_tpl_ab', '') or '')
                self.supports_server_date_range = ('{datum}' in tpl_ab)
            except Exception:
                self.supports_server_date_range = False

        # --- Politique d'éligibilité des DocIds ---
        # Objectif: éviter un cas piégeux en mode server-filtered days=N.
        # Quand le serveur filtre déjà par date, tout DocId renvoyé par la page est
        # supposé éligible. Si notre parsing "léger" de date se trompe (PDatum manquant
        # ou pris sur la mauvaise ligne), on ne doit PAS dropper le DocId.
        # => REQUIRE_ALL_DETECTED_ELIGIBLE force "DocIds détectés = DocIds éligibles".
        try:
            self.REQUIRE_ALL_DETECTED_ELIGIBLE = bool(self.config.get('require_all_detected_eligible', False))
        except Exception:
            self.REQUIRE_ALL_DETECTED_ELIGIBLE = False
        if self.days is not None and self.days > 0 and self.supports_server_date_range:
            # Default ON for server-filtered incremental runs
            self.REQUIRE_ALL_DETECTED_ELIGIBLE = True
        # If the canton config does not support publication sorting, disable it to avoid premature stop
        if self.sort == 'publication':
            sort_tokens = self.config.get('sort_tokens')
            has_pub_sort = False
            if isinstance(sort_tokens, dict) and ('publication' in sort_tokens or 'date_publication' in sort_tokens):
                has_pub_sort = True
            if self.config.get('ui_publication_sort_body') or self.config.get('result_query_tpl_sort') or self.config.get('result_query_tpl_ab_sort'):
                has_pub_sort = True
            if not has_pub_sort:
                # We keep sort='publication' as a logical mode for cutoff heuristics even if we cannot
                # actively request server-side sorting. Many Tribuna instances are already ordered by
                # publication by default, and the cutoff stop remains useful.
                logger.warning(
                    "Tri publication (--days): aucun template/jeton de tri dédié pour ce canton; on garde sort=publication pour l'heuristique cutoff (requêtes non forcées en tri serveur)."
                )
        
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
        try:
            self.PDF_HTTPSTATUS_RETRY = list(self.config.get('pdf_httpstatus_retry', [400, 401, 403, 404, 410, 500, 502, 503]))
        except Exception:
            self.PDF_HTTPSTATUS_RETRY = [400, 401, 403, 404, 410, 500, 502, 503]
        
        # Flags
        self.ENCRYPTED = self.config['encrypted']
        self.ASCII_ENCRYPTED = self.config['ascii_encrypted']
        self.COOKIE = self.config['needs_cookie']
        self.HOLE_AUCH_HTML = self.config['hole_auch_html']
        # STRICT_FULL: mode "validation/complet".
        # Il sert surtout à:
        # - ne pas différer le decrypt (on tente de récupérer les PDFs, même si c'est coûteux)
        # - éviter certaines heuristiques d'arrêt agressives
        # Note: days=N doit rester fiable même en STRICT_FULL (on garde les gardes anti hors-plage).
        self.STRICT_FULL = bool(strict_full)
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
        # Hard upper bound (today) when days filter is used
        self.max_date = self.today if self.days is not None and self.days > 0 else None
        # Limites pages/items: valeurs par défaut depuis config.
        # (Overrides possibles via settings Scrapy: -s MAX_PAGES=..., -s MAX_ITEMS=...)
        try:
            self.MAX_PAGES = int(self.config.get('max_pages', 0) or 0)
        except Exception:
            self.MAX_PAGES = 0

        # Compteur de pages effectivement traitées lors de ce run
        self.pages_processed = 0
        # Compteur d'items émis et plafond optionnel
        self.items_emitted = 0
        self.MAX_ITEMS = 0

        # Debug dumps: désactivés par défaut (pas d'env vars).
        # (Overrides possibles via settings Scrapy)
        self.DUMP_GWT_BODY = False
        self.DUMP_GWT_BODY_PAGES = 1
        self.DUMP_GWT_BODY_DIR = Path('output') / 'gwt_bodies'

        self.DUMP_GWT_RESPONSE = False
        self.DUMP_GWT_RESPONSE_PAGES = 1
        self.DUMP_GWT_RESPONSE_DIR = Path('output') / 'gwt_responses'
        
        # Headers
        self.HEADERS = self.config['headers']

        # Limite dynamique d'IDs par page (plus élevée en STRICT_FULL)
        try:
            self.max_ids_per_page = int(MAX_IDS_PER_PAGE)
        except Exception:
            self.max_ids_per_page = 25
        if self.STRICT_FULL and self.max_ids_per_page < 40:
            self.max_ids_per_page = 40

        # Si STRICT_FULL: forcer decrypt et neutraliser heuristiques d'arrêt.
        if self.STRICT_FULL:
            self.DEFER_DECRYPT = False

        # Precompile canton-specific dossier number patterns (fallback to defaults)
        self._num_patterns = list(DEFAULT_NUM_REGEXES)
        extra_patterns = self.config.get('num_patterns')
        if isinstance(extra_patterns, (list, tuple)):
            for pattern in extra_patterns:
                try:
                    compiled = re.compile(str(pattern))
                    # Prioritize canton overrides by putting them at the front
                    self._num_patterns.insert(0, compiled)
                except re.error as exc:
                    logger.warning(f"Invalid num pattern '{pattern}': {exc}")

        # Availability guard (pre-check to skip 404/5xx cantons)
        self.availability_check = self.config.get('availability_check', {})
        try:
            self.avail_path = str(self.availability_check.get('path', '') or '')
        except Exception:
            self.avail_path = ''
        try:
            self.avail_timeout = int(self.availability_check.get('timeout', 8))
        except Exception:
            self.avail_timeout = 8
        try:
            self.avail_allow_statuses = set(int(s) for s in self.availability_check.get('allow_statuses', [200]))
        except Exception:
            self.avail_allow_statuses = {200}
        self.avail_skip_on_unavailable = bool(self.availability_check.get('skip_on_unavailable', True))
        self.avail_follow_redirects = bool(self.availability_check.get('follow_redirects', True))
        try:
            self.avail_login_markers = [str(x).lower() for x in self.availability_check.get('login_markers', []) if x]
        except Exception:
            self.avail_login_markers = []
        
        # Allowed domains
        domain = self.config['base_url'].replace('https://', '').replace('http://', '').rstrip('/')
        self.allowed_domains = [domain]
        
        logger.info(f"Spider initialisé pour {self.config['name']} ({self.kanton_kurz})")
        if self.min_date:
            logger.info(f"Mode incrémental (--days): scraping depuis {self.min_date.isoformat()}")
        elif self.ab:
            logger.info(f"Mode incrémental (--ab): scraping depuis {self.ab}")
        else:
            logger.info("Mode initial: scraping complet")
            # Log sort mode
            if self.sort:
                logger.info(f"Sort mode: {self.sort}")

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
        # Bootstrap retries when GWT tokens are stale
        self._bootstrap_retry_count = 0
        self.MAX_BOOTSTRAP_RETRIES = 2

        # --- Barrière PDF par page ---
        # Problème: une page GWT retourne ~20 DocIds; pour chacun on peut déclencher
        # 0..1 requête decrypt, puis 1..k tentatives de téléchargement PDF.
        # Si on pagine trop vite, on multiplie les requêtes concurrentes et on perd
        # le contrôle (timeouts, ordering confus, logs illisibles).
        # Solution: on bloque l'émission de la requête "page suivante" tant que tous
        # les DocIds de la page courante n'ont pas fini (succès PDF ou échec final).
        self.PAGE_PDF_BARRIER = bool(self.ENCRYPTED) and bool(self.config.get('page_pdf_barrier', True))
        self._page_pending_docids = {}  # page_nr -> set(docid)
        self._page_next_request = {}  # page_nr -> Request
        self._page_next_emitted = set()  # pages for which next was emitted

    # --- Helpers ---
    def _can_emit_more(self) -> bool:
        """Retourne True si l'on peut encore émettre des items (MAX_ITEMS non atteint)."""
        try:
            mi = int(self.MAX_ITEMS)
        except Exception:
            mi = 0
        return (mi <= 0) or (self.items_emitted < mi)

    def _barrier_register(self, page_nr: int, docid: str) -> None:
        """Enregistre un DocId comme "pending" pour la barrière de sa page.

        Appelé juste avant de yield:
        - soit un item direct (pas de decrypt)
        - soit une Request (decrypt / candidate PDF)

        L'idée: chaque DocId doit avoir exactement un appel _barrier_done() quand
        il atteint l'état terminal.
        """
        if not getattr(self, 'PAGE_PDF_BARRIER', False):
            return
        if page_nr is None or not docid:
            return
        try:
            s = self._page_pending_docids.get(page_nr)
            if s is None:
                s = set()
                self._page_pending_docids[page_nr] = s
            s.add(docid)
        except Exception:
            return

    def _barrier_done(self, page_nr: int, docid: str):
        """Marque un DocId comme terminé et débloque la pagination si nécessaire.

        Retourne la Request de la page suivante si (et seulement si):
        - la barrière est active
        - il n'y a plus aucun DocId pending pour page_nr
        - la requête suivante a été préparée et pas encore émise

        Ce mécanisme évite d'avoir à gérer des Promises/awaits: on reste dans
        le modèle Scrapy (chaîne de callbacks) et on "retarde" juste le yield.
        """
        if not getattr(self, 'PAGE_PDF_BARRIER', False):
            return None
        if page_nr is None or not docid:
            return None
        try:
            s = self._page_pending_docids.get(page_nr)
            if s is not None:
                s.discard(docid)
                # Still pending DocIds: remain blocked
                if len(s) > 0:
                    return None
                # Fully drained
                self._page_pending_docids.pop(page_nr, None)
        except Exception:
            return None
        try:
            if page_nr in self._page_next_emitted:
                return None
            req = self._page_next_request.pop(page_nr, None)
            if req is not None:
                self._page_next_emitted.add(page_nr)
                return req
        except Exception:
            return None
        return None

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
        return gwt_utils.extract_tokens(gwt_text)

    def _find_docid_indexes(self, tokens: List[str]) -> List[int]:
        """Retourne les index des DocIds dans la liste de tokens."""
        return gwt_utils.find_docid_indexes(tokens, RE_ID)

    def _slice_tokens(self, tokens: List[str], idx: int) -> List[str]:
        """Retourne une fenêtre locale autour d'un index pour absorber les décalages."""
        start = max(0, idx - ROW_WINDOW_BEFORE)
        end = min(len(tokens), idx + ROW_WINDOW_AFTER)
        return tokens[start:end]

    def _select_docid_in_window(
        self,
        tokens: List[str],
        preferred_doc_id: Optional[str] = None,
        preferred_pos: Optional[int] = None,
    ) -> Tuple[Optional[str], Optional[int]]:
        """Select the correct DocId occurrence inside a token window.

        When a window contains multiple DocIds (common with wide windows), picking the first one can
        duplicate earlier rows and skip later ones. This helper prefers the expected DocId and/or the
        DocId closest to the preferred position.
        """
        if not tokens:
            return None, None

        id_positions: List[int] = []
        for i, v in enumerate(tokens):
            if self.reID.fullmatch((v or '').strip()):
                id_positions.append(i)

        if not id_positions:
            return None, None

        # Prefer exact expected DocId if present.
        if preferred_doc_id:
            expected_positions = [i for i in id_positions if tokens[i] == preferred_doc_id]
            if expected_positions:
                if preferred_pos is None:
                    chosen_pos = expected_positions[0]
                else:
                    chosen_pos = min(expected_positions, key=lambda i: abs(i - preferred_pos))
                return tokens[chosen_pos], chosen_pos

        # Otherwise pick the DocId closest to the preferred position (or the first one).
        if preferred_pos is None:
            chosen_pos = id_positions[0]
        else:
            chosen_pos = min(id_positions, key=lambda i: abs(i - preferred_pos))
        return tokens[chosen_pos], chosen_pos

    def _extract_row_meta(
        self,
        tokens: List[str],
        counters: Optional[Dict[str, int]] = None,
        *,
        preferred_doc_id: Optional[str] = None,
        preferred_id_pos: Optional[int] = None,
        row_end_hint: Optional[int] = None,
        page_content: Optional[str] = None,
        doc_id_pos: Optional[int] = None,
    ) -> Optional[Dict[str, Union[str, bool]]]:
        """Extract lightweight metadata from a row window without issuing network requests.

        Returns a dict with keys: 'DocId','Num','EDatum','PDatum','Pfad','NeuePfadsyntax' or None if no DocId found.
        This allows deciding whether to defer expensive decrypt requests.
        """
        # Select the intended doc id index within the window
        doc_id, id_pos = self._select_docid_in_window(tokens, preferred_doc_id, preferred_id_pos)
        if id_pos is None or not doc_id:
            if isinstance(counters, dict):
                counters['id_missing'] = counters.get('id_missing', 0) + 1
            return None

        doc_id = self._safe_get(tokens, id_pos, '')
        num = self._detect_num(tokens, id_pos, counters)
        pfad, neue = self._detect_pdf_path(tokens, counters)
        edatum, pdatum = self._detect_dates(
            tokens,
            id_pos,
            neue,
            counters,
            row_end_hint=row_end_hint,
            doc_id=doc_id,
            page_content=page_content,
            doc_id_pos=doc_id_pos,
        )

        return {
            'DocId': doc_id,
            'Num': num,
            'EDatum': edatum,
            'PDatum': pdatum,
            'Pfad': pfad,
            'NeuePfadsyntax': neue,
        }

    def _parse_date_any(self, value: Optional[str]) -> Optional[datetime.date]:
        v = (value or '').strip()
        if not v:
            return None
        try:
            if RE_DATUM.fullmatch(v):
                return datetime.strptime(v, '%Y-%m-%d').date()
            if RE_DATUM_CH.fullmatch(v):
                return datetime.strptime(v, '%d.%m.%Y').date()
        except Exception:
            return None
        return None

    def _search_pdatum_in_content(self, doc_id: str, content: str, doc_id_pos: Optional[int] = None) -> Optional[str]:
        """Fallback: recover a plausible publication date near DocId in raw GWT content.

        Returns ISO date string (YYYY-MM-DD) or None.
        """
        if not doc_id or not content:
            return None

        try:
            today = datetime.utcnow().date()
            future_cutoff = today + timedelta(days=0)
        except Exception:
            future_cutoff = None

        occurrences: List[int] = []
        seen = set()
        if doc_id_pos is not None:
            occurrences.append(doc_id_pos)
            seen.add(doc_id_pos)

        search_start = 0
        while True:
            idx = content.find(doc_id, search_start)
            if idx == -1:
                break
            if idx not in seen:
                occurrences.append(idx)
                seen.add(idx)
            search_start = idx + len(doc_id)

        # Prefer after-DocId matches; widen enough to catch long titles.
        for idx in occurrences:
            window = content[max(0, idx - 300): min(len(content), idx + 2200)]
            matches: List[str] = []
            try:
                matches.extend(RE_DATUM.findall(window))
            except Exception:
                pass
            try:
                matches.extend(RE_DATUM_CH.findall(window))
            except Exception:
                pass

            best_date = None
            for m in matches:
                d = self._parse_date_any(m)
                if d is None:
                    continue
                if future_cutoff is not None and d > future_cutoff:
                    continue
                if best_date is None or d > best_date:
                    best_date = d
            if best_date is not None:
                return best_date.isoformat()
        return None

    def _match_num_candidate(self, value: Optional[str]) -> Optional[str]:
        candidate = (value or '').strip()
        if not candidate:
            return None
        if RE_NUM1.fullmatch(candidate) or RE_NUM2.fullmatch(candidate):
            return candidate
        for pattern in self._num_patterns:
            if pattern.fullmatch(candidate):
                return candidate
        digits = sum(1 for ch in candidate if ch.isdigit())
        letters = sum(1 for ch in candidate if ch.isalpha())
        if digits >= 2 and letters >= 1 and any(sep in candidate for sep in (' ', '/', '_', '-', '.')):
            return candidate
        return None

    def _detect_num(self, tokens: List[str], id_pos: int, counters: Optional[Dict[str, int]]) -> str:
        """Détecte un numéro de dossier adjacent à l'ID, sinon scanne toute la fenêtre."""
        window_start = max(0, id_pos - NUM_SEARCH_RADIUS)
        window_end = min(len(tokens), id_pos + NUM_SEARCH_RADIUS + 1)
        priority_offsets = [2, 3, 1, 4, 0, -1, -2, 5, -3]
        search_indices: List[int] = []
        seen = set()

        for offset in priority_offsets:
            idx = id_pos + offset
            if window_start <= idx < window_end and idx not in seen:
                search_indices.append(idx)
                seen.add(idx)

        for idx in range(window_start, window_end):
            if idx not in seen:
                search_indices.append(idx)
                seen.add(idx)

        for idx in search_indices:
            num_val = self._match_num_candidate(self._safe_get(tokens, idx, ''))
            if num_val:
                return num_val

        for span in range(2, MAX_JOINED_TOKENS + 1):
            for idx in search_indices:
                end = idx + span
                if end > len(tokens):
                    continue
                joined = ' '.join(t for t in tokens[idx:end] if t)
                if not joined:
                    continue
                num_val = self._match_num_candidate(joined)
                if num_val:
                    return num_val

        if counters is not None and isinstance(counters, dict):
            counters['num_missing'] = counters.get('num_missing', 0) + 1
        return ""

    def _search_pdf_path_in_content(self, doc_id: str, content: str, doc_id_pos: Optional[int] = None) -> Tuple[Optional[str], bool]:
        """Recherche un chemin PDF dans le contenu GWT autour d'un DocId."""
        if not doc_id or not content:
            return None, False

        occurrences: List[int] = []
        seen = set()
        if doc_id_pos is not None:
            occurrences.append(doc_id_pos)
            seen.add(doc_id_pos)

        search_start = 0
        while True:
            idx = content.find(doc_id, search_start)
            if idx == -1:
                break
            if idx not in seen:
                occurrences.append(idx)
                seen.add(idx)
            search_start = idx + len(doc_id)

        def find_in_windows(index: int) -> Tuple[Optional[str], bool]:
            window_after = content[index: min(len(content), index + 900)]
            for pattern, flag in ((RE_PFAD, False), (RE_PFAD2, True)):
                match = pattern.search(window_after)
                if match:
                    return match.group(0), flag
            window_before = content[max(0, index - 600): index]
            for pattern, flag in ((RE_PFAD, False), (RE_PFAD2, True)):
                match = pattern.search(window_before)
                if match:
                    return match.group(0), flag
            return None, False

        for idx in occurrences:
            pfad, flag = find_in_windows(idx)
            if pfad:
                return pfad, flag
        return None, False

    def _detect_pdf_path(
        self,
        tokens: List[str],
        counters: Optional[Dict[str, int]],
        doc_id: Optional[str] = None,
        page_content: Optional[str] = None,
        doc_id_pos: Optional[int] = None,
    ) -> Tuple[Optional[str], bool]:
        """Détecte le chemin PDF (classique ou chiffré) dans la fenêtre locale."""
        neuePfadsyntax = False
        pfad: Optional[str] = None
        for idx in range(0, len(tokens)):
            tok = tokens[idx] or ''
            if RE_PFAD.fullmatch(tok):
                pfad = tok
                break
            elif RE_PFAD2.fullmatch(tok):
                pfad = tok
                neuePfadsyntax = True
                break
        if not pfad and doc_id and page_content:
            fallback, fallback_neue = self._search_pdf_path_in_content(doc_id, page_content, doc_id_pos)
            if fallback:
                pfad = fallback
                neuePfadsyntax = fallback_neue or neuePfadsyntax
                logger.debug(f"Pfad fallback détecté pour DocID {doc_id}: {pfad[:32]}...")
        if not pfad and isinstance(counters, dict):
            counters['pfad_missing'] = counters.get('pfad_missing', 0) + 1
        return pfad, neuePfadsyntax

    def _collect_docid_positions(self, content: str) -> Dict[str, List[int]]:
        """Prépare un mapping DocId -> positions dans le contenu pour les recherches fallback."""
        positions: Dict[str, List[int]] = {}
        if not content:
            return positions
        for match in self.reID.finditer(content):
            doc_id = match.group()
            positions.setdefault(doc_id, []).append(match.start())
        return positions

    def _detect_dates(
        self,
        tokens: List[str],
        id_pos: int,
        neuePfadsyntax: bool,
        counters: Optional[Dict[str, int]],
        *,
        row_end_hint: Optional[int] = None,
        doc_id: Optional[str] = None,
        page_content: Optional[str] = None,
        doc_id_pos: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Détecte la date de décision (EDatum) et la date de publication (PDatum)."""
        entscheiddatum: str = ""
        publikationsdatum: str = ""
        base_date_idx = id_pos + 3

        # Future cutoff to reject bogus future dates (tolerance fixed to 0 days).
        try:
            today = datetime.utcnow().date()
            future_cutoff = today + timedelta(days=0)
        except Exception:
            future_cutoff = None

        # Determine the row boundary.
        # IMPORTANT: relying on the first token that looks like a DocId is fragile because
        # some rows include other 32-hex tokens (e.g. encrypted paths). Prefer a caller-provided
        # hint based on the next known DocId index in the page.
        if isinstance(row_end_hint, int) and row_end_hint > 0:
            row_end = min(len(tokens), row_end_hint)
        else:
            row_end = len(tokens)
            try:
                for j in range(id_pos + 1, len(tokens)):
                    if self.reID.fullmatch((tokens[j] or '').strip()):
                        row_end = j
                        break
            except Exception:
                row_end = len(tokens)

        # Detect EDatum (decision date). Some instances emit dates as DD.MM.YYYY;
        # normalize everything to ISO (YYYY-MM-DD).
        if base_date_idx < row_end:
            d0 = self._parse_date_any(tokens[base_date_idx])
            if d0 is not None:
                entscheiddatum = d0.isoformat()
        if not entscheiddatum and base_date_idx + 1 < row_end:
            d1 = self._parse_date_any(tokens[base_date_idx + 1])
            if d1 is not None:
                entscheiddatum = d1.isoformat()
        if not entscheiddatum:
            for off in range(0, min(20, max(0, row_end - base_date_idx))):
                idxd = base_date_idx + off
                if idxd < row_end:
                    dd = self._parse_date_any(tokens[idxd])
                    if dd is not None:
                        entscheiddatum = dd.isoformat()
                        break

        # If an EDatum was found, filter out obviously-future dates.
        if entscheiddatum:
            try:
                ed_d = self._parse_date_any(entscheiddatum)
            except Exception:
                ed_d = None
            try:
                if ed_d is not None and future_cutoff is not None and ed_d > future_cutoff:
                    entscheiddatum = ''
                    if isinstance(counters, dict):
                        counters['edatum_missing'] = counters.get('edatum_missing', 0) + 1
            except Exception:
                pass

        # Adjust row length if neuePfadsyntax (some layouts append extra fields)
        l = max(0, row_end)
        if neuePfadsyntax and l > 22:
            l = l - 8

        # Collect potential publication dates by heuristics (row-local)
        candidates: List[str] = []
        for ti in [l - 1, l - 2, l - 3]:
            if 0 <= ti < row_end:
                dd = self._parse_date_any(tokens[ti])
                if dd is not None:
                    candidates.append(dd.isoformat())
        if not candidates:
            start_pub_idx = base_date_idx + 2
            for off in range(0, 8):
                idxp = start_pub_idx + off
                if idxp < row_end:
                    dd = self._parse_date_any(tokens[idxp])
                    if dd is not None:
                        candidates.append(dd.isoformat())
        if not candidates:
            for idxp in range(id_pos, row_end):
                dd = self._parse_date_any(tokens[idxp])
                if dd is None:
                    continue
                iso = dd.isoformat()
                if entscheiddatum and iso == entscheiddatum:
                    continue
                candidates.append(iso)
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
                    d = self._parse_date_any(c)
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
                        logger.debug(f"PDatum candidates: {[c for _,c,_ in scored]} chosen={chosen}")
                    except Exception:
                        pass
        publikationsdatum = chosen or (candidates[0] if candidates else '')

        # Raw-content fallback: if missing OR appears older than min_date in --days mode,
        # try to recover a better PDatum close to the DocId occurrence.
        try:
            needs_fallback = False
            if not publikationsdatum:
                needs_fallback = True
            elif self.min_date:
                pd_dt = None
                try:
                    pd_dt = self._parse_date_any(publikationsdatum)
                except Exception:
                    pd_dt = None
                if pd_dt is not None and pd_dt < self.min_date:
                    needs_fallback = True
            if needs_fallback and doc_id and page_content:
                fallback = self._search_pdatum_in_content(doc_id, page_content, doc_id_pos)
                if fallback:
                    publikationsdatum = fallback
        except Exception:
            pass

        # Enforce upper bound (today) when max_date is set (mode --days)
        if self.max_date:
            try:
                if publikationsdatum:
                    pd_dt = self._parse_date_any(publikationsdatum)
                    if pd_dt > self.max_date:
                        publikationsdatum = ''
                if entscheiddatum:
                    ed_dt = self._parse_date_any(entscheiddatum)
                    if ed_dt > self.max_date:
                        entscheiddatum = ''
            except Exception:
                pass
        return entscheiddatum or "", publikationsdatum or ""

    def _build_pdf_candidates_from_decrypt(self, response_text: str, item: dict) -> Optional[Tuple[str, List[str]]]:
        """Construit les URLs candidates PDF à partir du texte de décryptage.

        Retourne (first_url, other_candidates) ou None si insuffisant.
        """
        dossier_from_item = (item.get('Num') or '').replace(' ', '_')
        base = self.config.get('base_url', '').rstrip('/') or self.DOWNLOAD_URL.rstrip('/')
        return gwt_utils.build_pdf_candidates_from_decrypt(
            response_text=response_text,
            download_url=self.DOWNLOAD_URL,
            base_url=base,
            dossier_fallback=dossier_from_item,
        )

    def _is_pdf_ok(self, response: Response) -> bool:
        """Renvoie True si la réponse contient un PDF (status 200/206 + content-type/application/pdf)."""
        return gwt_utils.is_pdf_ok(response)

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        """Instancie le spider et récupère tôt les settings (MAX_PAGES, etc.)."""
        # Propager STRICT_FULL depuis les settings s'il n'est pas passé en argument
        strict_flag = False
        try:
            strict_flag = crawler.settings.getbool('STRICT_FULL', False)
        except Exception:
            strict_flag = False
        spider = super(TribunaGraubundenSpider, cls).from_crawler(crawler, *args, strict_full=strict_flag, **kwargs)
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

        # Debug dump knobs (optional)
        try:
            spider.DUMP_GWT_BODY = crawler.settings.getbool('DUMP_GWT_BODY', False)
        except Exception:
            spider.DUMP_GWT_BODY = False
        try:
            spider.DUMP_GWT_BODY_PAGES = crawler.settings.getint('DUMP_GWT_BODY_PAGES', 1)
        except Exception:
            spider.DUMP_GWT_BODY_PAGES = 1
        try:
            body_dir = crawler.settings.get('DUMP_GWT_BODY_DIR', None)
            if body_dir:
                spider.DUMP_GWT_BODY_DIR = Path(str(body_dir)).expanduser()
        except Exception:
            pass

        try:
            spider.DUMP_GWT_RESPONSE = crawler.settings.getbool('DUMP_GWT_RESPONSE', False)
        except Exception:
            spider.DUMP_GWT_RESPONSE = False
        try:
            spider.DUMP_GWT_RESPONSE_PAGES = crawler.settings.getint('DUMP_GWT_RESPONSE_PAGES', 1)
        except Exception:
            spider.DUMP_GWT_RESPONSE_PAGES = 1
        try:
            resp_dir = crawler.settings.get('DUMP_GWT_RESPONSE_DIR', None)
            if resp_dir:
                spider.DUMP_GWT_RESPONSE_DIR = Path(str(resp_dir)).expanduser()
        except Exception:
            pass

        return spider
    
    async def start(self):
        """Point d’entrée effectif du spider (async, Scrapy 2.13+).

        Séquence:
        1) (optionnel) Availability check: un petit GET pour éviter de bloquer sur un portail HS.
        2) Bootstrap (GET): récupérer les tokens GWT dynamiques (Permutation / Module-Base).
        3) Première requête GWT-RPC (POST loadTable) vers `result_page_url`.

        Pourquoi ce bootstrap est indispensable:
        - Sur GWT-RPC, si `X-GWT-Permutation` est obsolète, le serveur renvoie des erreurs
          d'incompatibilité (ex: `IncompatibleRemoteServiceException`).
        """
        # Si Scrapy a reçu un setting MAX_PAGES via -s, l'utiliser en priorité.
        try:
            if hasattr(self, 'crawler') and self.crawler and self.crawler.settings:
                s_val = self.crawler.settings.getint('MAX_PAGES', fallback=None)
                if s_val:
                    self.MAX_PAGES = s_val
        except Exception:
            pass

        # Log stable (baseline): configuration effective de run
        try:
            _min_date = self.min_date.isoformat() if getattr(self, 'min_date', None) else None
        except Exception:
            _min_date = None
        try:
            _max_date = self.max_date.isoformat() if getattr(self, 'max_date', None) else None
        except Exception:
            _max_date = None
        try:
            logger.info(
                "Run config: canton=%s kurz=%s days=%s min_date=%s max_date=%s sort=%s supports_server_date_range=%s require_all_detected_eligible=%s STRICT_FULL=%s MAX_PAGES=%s MAX_ITEMS=%s"
                % (
                    getattr(self, 'canton', None),
                    getattr(self, 'kanton_kurz', None),
                    getattr(self, 'days', None),
                    _min_date,
                    _max_date,
                    getattr(self, 'sort', None),
                    getattr(self, 'supports_server_date_range', None),
                    getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', None),
                    getattr(self, 'STRICT_FULL', None),
                    getattr(self, 'MAX_PAGES', None),
                    getattr(self, 'MAX_ITEMS', None),
                )
            )
        except Exception:
            pass

        # Préférer la page d'accueil (base_url) pour récupérer les tokens GWT ;
        # certains modules (ex: /tribunavtplus/) retournent 404 en GET direct.
        bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)

        # Availability guard: léger GET avant bootstrap pour éviter 404/5xx bloquants
        availability_url = bootstrap_url
        try:
            if self.avail_path:
                availability_url = urljoin(self.config.get('base_url', bootstrap_url), self.avail_path)
        except Exception:
            availability_url = bootstrap_url

        if self.availability_check or self.avail_skip_on_unavailable:
            try:
                self.crawler.stats.inc_value('tribuna/availability_checks_scheduled')
            except Exception:
                pass
            yield scrapy.Request(
                url=availability_url,
                method="GET",
                headers=self.HEADERS,
                callback=self.parse_availability,
                errback=self.errback_availability,
                meta={
                    'referrer_policy': "no-referrer",
                    'bootstrap_url': bootstrap_url,
                    'handle_httpstatus_all': True,
                    'dont_redirect': not self.avail_follow_redirects,
                    'download_timeout': self.avail_timeout,
                },
                dont_filter=True
            )
            return

        yield scrapy.Request(
            url=bootstrap_url,
            method="GET",
            headers=self.HEADERS,
            callback=self.parse_bootstrap,
            errback=self.errback_httpbin,
            meta={'referrer_policy': "no-referrer", 'bootstrap': True},
            dont_filter=True
        )

    def parse_availability(self, response):
        """Availability pre-check: skip canton on 404/5xx (or login marker) before bootstrap."""
        try:
            self.crawler.stats.inc_value('tribuna/availability_checks')
        except Exception:
            pass

        status_ok = response.status in self.avail_allow_statuses
        reason = f"status={response.status}"

        if status_ok and self.avail_login_markers:
            try:
                body_low = (response.text or '').lower()
            except Exception:
                body_low = ''
            for marker in self.avail_login_markers:
                if marker and marker in body_low:
                    status_ok = False
                    reason = f"login-marker:{marker}"
                    break

        if status_ok:
            try:
                self.crawler.stats.inc_value('tribuna/availability_checks_ok')
            except Exception:
                pass
            logger.info(f"Availability OK (status {response.status}); bootstrap.")
            bootstrap_url = response.meta.get('bootstrap_url') or self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)
            yield scrapy.Request(
                url=bootstrap_url,
                method="GET",
                headers=self.HEADERS,
                callback=self.parse_bootstrap,
                errback=self.errback_httpbin,
                meta={'referrer_policy': "no-referrer", 'bootstrap': True, 'handle_httpstatus_all': True},
                dont_filter=True
            )
            return

        logger.warning(f"Availability FAILED ({reason}); canton {self.canton}")
        if self.avail_skip_on_unavailable:
            try:
                self.crawler.stats.inc_value('tribuna/cantons_skipped')
            except Exception:
                pass
            return None

        bootstrap_url = response.meta.get('bootstrap_url') or self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)
        logger.info("Proceeding with bootstrap despite availability failure (skip_on_unavailable=False)")
        yield scrapy.Request(
            url=bootstrap_url,
            method="GET",
            headers=self.HEADERS,
            callback=self.parse_bootstrap,
            errback=self.errback_httpbin,
            meta={'referrer_policy': "no-referrer", 'bootstrap': True, 'handle_httpstatus_all': True},
            dont_filter=True
        )

    async def errback_availability(self, failure):
        """Errback for availability pre-check."""
        try:
            self.crawler.stats.inc_value('tribuna/availability_checks_failed')
        except Exception:
            pass
        self._log_failure(failure, "Availability check failed")
        if self.avail_skip_on_unavailable:
            try:
                self.crawler.stats.inc_value('tribuna/cantons_skipped')
            except Exception:
                pass
            return None

        bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)
        return scrapy.Request(
            url=bootstrap_url,
            method="GET",
            headers=self.HEADERS,
            callback=self.parse_bootstrap,
            errback=self.errback_httpbin,
            meta={'referrer_policy': "no-referrer", 'bootstrap': True},
            dont_filter=True
        )

    def _log_failure(self, failure, message: str) -> None:
        """Centralized errback logging with Scrapy-friendly formatting."""
        try:
            req = getattr(failure, 'request', None)
        except Exception:
            req = None
        try:
            url = getattr(req, 'url', None)
            method = getattr(req, 'method', None)
        except Exception:
            url = None
            method = None
        try:
            logger.error(
                "%s (method=%s url=%s)",
                message,
                method,
                url,
                exc_info=failure_to_exc_info(failure),
            )
        except Exception:
            try:
                logger.error("%s (method=%s url=%s)", message, method, url)
            except Exception:
                pass
    
    def get_next_request(self):
        """Construit le body GWT-RPC `loadTable` pour la page suivante.

        Notes importantes pour quelqu'un qui découvre GWT:
        - Le body est un format GWT-RPC (pas du JSON). Il encode une méthode Java,
          des paramètres, des index, etc.
        - La structure exacte dépend du canton (UI différente). On stocke donc des
          templates capturés depuis l'UI dans `cantons_config.json`.
        - La variable `millis` est souvent présente dans les bodies UI (timestamp).
          Certaines instances semblent accepter n'importe quelle valeur, mais on
          reproduit l'UI pour éviter des comportements divergents.
        """
        logger.info(f"Génération de la requête pour la page {self.page_nr}")
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
            # Mode days=N: on essaie d'exploiter le filtre UI côté serveur quand il existe.
            # Selon le canton, `{datum}` doit contenir:
            # - soit une plage "dd.mm.yyyy - dd.mm.yyyy" (mode `range`)
            # - soit une date unique (mode `single`)
            # Si le canton ne supporte pas le filtre serveur (pas de {datum}), on fera
            # plutôt un stop côté parse (tri publication + cutoff), mais on garde quand
            # même ce code car plusieurs cantons ont des variantes.
            date_mode = str(self.config.get('date_filter_mode', 'range') or 'range').strip().lower()
            fmt = self.config.get('date_range_format')
            end_date = self.max_date or self.today
            try:
                if date_mode == 'single':
                    datum = self.min_date.strftime(fmt) if fmt else self.min_date.isoformat()
                else:
                    if fmt:
                        datum = f"{self.min_date.strftime(fmt)} - {end_date.strftime(fmt)}"
                    else:
                        datum = f"{self.min_date.isoformat()} - {end_date.isoformat()}"
            except Exception:
                datum = self.min_date.isoformat()
            # Prefer a sort-aware template if provided
            if sort_token and 'result_query_tpl_ab_sort' in self.config:
                body = self.config['result_query_tpl_ab_sort'].format(page_nr=self.page_nr, millis=millis, datum=datum, sort_token=sort_token)
                logger.info(f"Template: result_query_tpl_ab_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL_AB.format(page_nr=self.page_nr, millis=millis, datum=datum)
                logger.info(f"Template: result_query_tpl_ab; sort=None; page={self.page_nr}")
        elif self.ab is None:
            # If a dedicated UI-captured body is provided for publication sort, prefer it when no date filter is used
            ui_pub_sort = self.config.get('ui_publication_sort_body')
            if sort_key_used == 'date_publication' and ui_pub_sort and self.min_date is None:
                try:
                    body = ui_pub_sort.format(page_nr=self.page_nr, millis=millis)
                except Exception:
                    body = ui_pub_sort
                logger.info(f"Template: ui_publication_sort_body; sort={sort_key_used}; page={self.page_nr}")
            elif sort_token and 'result_query_tpl_sort' in self.config:
                body = self.config['result_query_tpl_sort'].format(page_nr=self.page_nr, millis=millis, sort_token=sort_token)
                logger.info(f"Template: result_query_tpl_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL.format(page_nr=self.page_nr, millis=millis)
                logger.info(f"Template: result_query_tpl; sort=None; page={self.page_nr}")
        else:
            if sort_token and 'result_query_tpl_ab_sort' in self.config:
                body = self.config['result_query_tpl_ab_sort'].format(page_nr=self.page_nr, millis=millis, datum=self.ab, sort_token=sort_token)
                logger.info(f"Template: result_query_tpl_ab_sort; sort={sort_key_used}; page={self.page_nr}")
            else:
                body = self.RESULT_QUERY_TPL_AB.format(page_nr=self.page_nr, millis=millis, datum=self.ab)
                logger.info(f"Template: result_query_tpl_ab; sort=None; page={self.page_nr}")
        
        # Dump body for diagnostics (before incrementing page_nr)
        current_page = self.page_nr
        if self.DUMP_GWT_BODY and current_page < max(0, self.DUMP_GWT_BODY_PAGES):
            try:
                ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
                out_dir = Path(self.DUMP_GWT_BODY_DIR)
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"gwt_body_{self.canton}_{ts}_p{current_page}.txt"
                out_path.write_text(body, encoding='utf-8', errors='ignore')
                logger.info(f"GWT body dump: {out_path}")
            except Exception as exc:
                logger.warning(f"Impossible d'écrire le dump GWT body: {exc}")

        self.page_nr += 1
        return body
    
    async def set_cookie(self, response):
        """Gère l'initialisation du cookie si nécessaire"""
        logger.info(f"Initialisation du cookie pour {self.COOKIE_INIT}")
        
        if response.status == 200:
            logger.info(f"Headers reçus: {response.headers}")
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
                    logger.debug(f"Erreur décodage Set-Cookie: {e}")
            if cookie_parts:
                cookie_header = '; '.join(cookie_parts)
                request = response.meta['request']
                # Assigner en tant que chaîne (Scrapy gère l'encodage)
                request.headers['Cookie'] = cookie_header
                logger.info("Cookie défini, lancement requête avec cookie")
                yield request
            else:
                logger.warning("Aucun cookie valide reçu, tentative sans cookie")
                yield response.meta['request']
        else:
            logger.error(f"Erreur initialisation cookie: status {response.status}")

    async def parse_bootstrap(self, response):
        """Analyse la page initiale pour extraire permutation/module GWT dynamiques.

        Pourquoi c'est crucial:
        - GWT compile l'app en JS et ajoute un "strongName" (= permutation). Le serveur
          refuse les requêtes GWT-RPC si X-GWT-Permutation est mauvais (erreur RPC).
        - Sur Tribuna, ce token peut changer sans prévenir: on le redécouvre à chaque run.

        Stratégie:
        1) Essayer de le lire dans le HTML (script *.cache.js ou inline strongName)
        2) Sinon, suivre un *.nocache.js et y extraire une référence vers *.cache.js
        3) Si tout échoue, on conserve la permutation statique du config (best-effort)
        """
        # Even on non-200, try to parse best-effort (some proxies return 3xx/4xx with HTML).
        try:
            if getattr(response, 'status', 200) != 200:
                logger.warning("Bootstrap GET returned status=%s; attempting best-effort token extraction", getattr(response, 'status', None))
        except Exception:
            pass

        nocache_urls = self._update_gwt_tokens(response)

        # If permutation is still missing, follow the nocache.js via Scrapy (no requests.get)
        if 'X-GWT-Permutation' not in self.HEADERS and nocache_urls:
            yield response.follow(
                url=nocache_urls[0],
                method="GET",
                headers=self.HEADERS,
                callback=self.parse_bootstrap_nocache,
                errback=self.errback_httpbin,
                meta={'referrer_policy': "no-referrer", 'bootstrap_nocache': True, 'handle_httpstatus_all': True},
                dont_filter=True,
            )
            return
        # Si la permutation reste absente et un module-base existe, tenter un GET dessus en secours
        if 'X-GWT-Permutation' not in self.HEADERS and 'X-GWT-Module-Base' in self.HEADERS:
            mb = self.HEADERS['X-GWT-Module-Base']
            logger.debug(f"Fallback GET sur module base: {mb}")
            yield scrapy.Request(
                url=mb,
                method="GET",
                headers=self.HEADERS,
                callback=self.parse_bootstrap_module,
                errback=self.errback_httpbin,
                meta={'referrer_policy': "no-referrer", 'bootstrap_module': True, 'handle_httpstatus_all': True},
                dont_filter=True
            )
            return
        # Ensuite lancer la première requête POST vers loadTable
        for req in self._issue_first_post():
            yield req

    async def parse_bootstrap_module(self, response):
        """Analyse la page du module-base si la page d'accueil ne donnait pas de permutation."""
        try:
            if getattr(response, 'status', 200) != 200:
                logger.warning("Bootstrap module GET returned status=%s; attempting best-effort token extraction", getattr(response, 'status', None))
        except Exception:
            pass
        nocache_urls = self._update_gwt_tokens(response)
        if 'X-GWT-Permutation' not in self.HEADERS and nocache_urls:
            yield response.follow(
                url=nocache_urls[0],
                method="GET",
                headers=self.HEADERS,
                callback=self.parse_bootstrap_nocache,
                errback=self.errback_httpbin,
                meta={'referrer_policy': "no-referrer", 'bootstrap_nocache': True, 'handle_httpstatus_all': True},
                dont_filter=True,
            )
            return
        for req in self._issue_first_post():
            yield req

    async def parse_bootstrap_nocache(self, response: Response):
        """Parse un fichier *.nocache.js pour extraire la permutation GWT et lancer le premier POST."""
        try:
            if getattr(response, 'status', 200) != 200:
                logger.warning("Bootstrap nocache GET returned status=%s; continuing with static tokens if needed", getattr(response, 'status', None))
        except Exception:
            pass
        try:
            text_nc = response.text or ''
        except Exception:
            text_nc = ''

        found_perm = None
        try:
            strong = re.search(r'strongName\s*[:=]\s*[\"\']([A-Za-z0-9._-]{8,})[\"\']', text_nc)
            if strong:
                found_perm = strong.group(1)
        except Exception:
            found_perm = None

        if not found_perm:
            try:
                cache_hits = re.findall(r'([A-Za-z0-9._/-]+)\.cache\.js', text_nc)
                if cache_hits:
                    found_perm = cache_hits[0].split('/')[-1].split('.')[0]
            except Exception:
                pass

        # Tribuna-specific fallback: hex32 values embedded in nocache
        if not found_perm:
            try:
                hex32 = re.findall(r'"([A-F0-9]{32})"', text_nc) or re.findall(r"'([A-F0-9]{32})'", text_nc)
                if hex32:
                    found_perm = hex32[0]
                    logger.debug(f"Permutation extraite depuis hex32 nocache: {found_perm}")
            except Exception:
                pass

        if found_perm:
            try:
                self.HEADERS['X-GWT-Permutation'] = found_perm
                logger.info(f"X-GWT-Permutation dynamique détecté: {found_perm}")
            except Exception:
                pass

        # Module-base fallback: infer from current nocache URL
        if 'X-GWT-Module-Base' not in self.HEADERS:
            try:
                self.HEADERS['X-GWT-Module-Base'] = str(response.url).rsplit('/', 1)[0] + '/'
                logger.info(f"X-GWT-Module-Base dynamique détecté: {self.HEADERS['X-GWT-Module-Base']}")
            except Exception:
                pass

        if 'X-GWT-Permutation' not in self.HEADERS:
            try:
                snippet_nc = text_nc[:400].replace('\n', ' ').replace('\r', ' ')
                logger.warning(f"Permutation GWT non détectée dans nocache; utilisation des valeurs statiques de config; snippet nocache={snippet_nc}")
            except Exception:
                logger.warning("Permutation GWT non détectée dans nocache; utilisation des valeurs statiques de config")

        for req in self._issue_first_post():
            yield req

    def _issue_first_post(self):
        """Envoie la première requête POST avec les tokens GWT mis à jour."""
        body = self.get_next_request()
        current_page = self.page_nr - 1

        if self.COOKIE:
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

    def _update_gwt_tokens(self, response: Response) -> List[str]:
        """Met à jour les headers GWT (Permutation + Module-Base).

        - X-GWT-Permutation: identifiant de build GWT ("strongName")
        - X-GWT-Module-Base: base URL du module (sert de Referer + parfois requis côté serveur)

        Implémentation:
        - Parse le HTML pour trouver *.cache.js (le nom du fichier contient souvent strongName)
                - Sinon, suivre un *.nocache.js via Scrapy (pas de `requests.get`).
        """
        try:
            html = response.text or ""
        except Exception:
            html = ""

        base_url = getattr(response, 'url', self.config.get('base_url', ''))
        found_perm, found_module, nocache_urls = gwt_utils.extract_bootstrap_tokens(
            html,
            base_url=base_url,
            response=response,
        )

        if found_perm:
            try:
                self.HEADERS['X-GWT-Permutation'] = found_perm
                logger.info(f"X-GWT-Permutation dynamique détecté: {found_perm}")
            except Exception:
                pass
        if found_module:
            try:
                self.HEADERS['X-GWT-Module-Base'] = found_module
                logger.info(f"X-GWT-Module-Base dynamique détecté: {found_module}")
            except Exception:
                pass

        if not found_perm:
            try:
                snippet = (html[:400] if html else '').replace('\n', ' ').replace('\r', ' ')
                logger.warning(f"Permutation GWT non détectée; utilisation des valeurs statiques de config; snippet bootstrap={snippet}")
            except Exception:
                logger.warning("Permutation GWT non détectée; utilisation des valeurs statiques de config")
        else:
            # Reset bootstrap retry counter once we lock a permutation
            self._bootstrap_retry_count = 0

        return nocache_urls
    
    async def parse_page(self, response):
        """Parse une page de résultats GWT et émet items + requête de page suivante.

        Pipeline de cette méthode:
        1) Détecter des erreurs RPC liées à des tokens GWT obsolètes.
           -> Si c'est le cas, re-bootstrap puis re-tenter la même page.
        2) Extraire `trefferzahl` (nombre total) et tokeniser la réponse.
        3) Identifier les DocIds, puis pour chaque DocId:
           - extraire un "meta" léger (dates, pfad, num) pour décider de faire un decrypt
           - appliquer la logique days=N (éligibilité/cutoff)
           - faire le parsing complet et éventuellement déclencher decrypt/GET PDF
        4) Pagination:
           - si PAGE_PDF_BARRIER: la requête page suivante est stockée et yieldée
             seulement quand tous les DocIds de la page sont terminés.
        """
        # Récupérer le numéro de page transmis via meta (fiable).
        current_page = response.meta.get('page_nr', self.page_nr - 1)
        logger.info(f"Parsing page {current_page}")
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
            logger.debug(f"Requête complète (page {current_page}): {body_str}")
        # Détecter tôt les erreurs GWT d'incompatibilité.
        # Typiquement: IncompatibleRemoteServiceException -> permutation ou module-base invalide
        # après un déploiement côté serveur.
        try:
            body_preview = response.text[:600].replace('\n', ' ').replace('\r', ' ')
        except Exception:
            body_preview = ''
        if 'IncompatibleRemoteServiceException' in body_preview or 'strongName' in body_preview:
            if self._bootstrap_retry_count < self.MAX_BOOTSTRAP_RETRIES:
                self._bootstrap_retry_count += 1
                logger.warning(
                    f"IncompatibleRemoteServiceException détectée; re-bootstrap {self._bootstrap_retry_count}/{self.MAX_BOOTSTRAP_RETRIES}"
                )
                bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)
                # Revenir à la page courante après re-bootstrap
                self.page_nr = current_page
                # Log a short snippet of the RPC error to capture the expected signature
                try:
                    snippet_err = body_preview[:280]
                    logger.debug(f"RPC error snippet: {snippet_err}")
                    # Dump full RPC error for offline analysis
                    try:
                        out_dir = Path(__file__).parent.parent / 'output' / 'debug_rpc'
                        out_dir.mkdir(parents=True, exist_ok=True)
                        ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
                        fname = out_dir / f"rpc_error_page{current_page}_{ts}.txt"
                        with open(fname, 'w', encoding='utf-8') as fh:
                            fh.write("=== RESPONSE ===\n")
                            fh.write(response.text)
                            fh.write("\n\n=== REQUEST BODY ===\n")
                            fh.write(body_str)
                        logger.info(f"RPC error dump écrit: {fname}")
                    except Exception as e:
                        logger.debug(f"Impossible d'écrire le dump RPC: {e}")
                except Exception:
                    pass
                yield scrapy.Request(
                    url=bootstrap_url,
                    method="GET",
                    headers=self.HEADERS,
                    callback=self.parse_bootstrap,
                    errback=self.errback_httpbin,
                    meta={'referrer_policy': "no-referrer", 'bootstrap_retry': True},
                    dont_filter=True
                )
                return
            else:
                logger.error("IncompatibleRemoteServiceException persiste après re-bootstrap; arrêt")
        # Extraire et logguer l'index de page du payload (slot |20|x|-1|) pour INFO
        try:
            m_page = re.search(r"\|20\|(-?\d+)\|\-1\|", body_str)
            if m_page:
                logger.info(f"Payload page slot (|20|x|-1|): {m_page.group(1)}")
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
                    logger.debug(f"Champs numériques modifiés aux index: {diff_idx[:10]}")
                else:
                    logger.debug("Aucun champ numérique du body n'a changé vs page précédente")
            except Exception:
                pass
        if ints:
            self._prev_req_ints = ints
        
        if response.status == 200 and len(response.body) >= MINIMUM_PAGE_LEN:
            # Extraire le nombre total de résultats
            # Utiliser current_page (0-based) ou si trefferzahl encore inconnu
            if current_page == 0 or self.trefferzahl == 0:
                tz = gwt_utils.extract_trefferzahl(response.text)
                if tz is not None:
                    self.trefferzahl = tz
                    logger.info(f"Nombre total de résultats: {self.trefferzahl}")
                else:
                    # Debug: log beginning of response when GWT prefix not found (first page only)
                    try:
                        if current_page == 0:
                            snippet = response.text[:800].replace('\n', ' ').replace('\r', ' ')
                            logger.debug(f"Réponse (début) sans préfixe GWT attendu: {snippet}")
                    except Exception:
                        pass
            
            if self.trefferzahl > 0:
                # Dump raw GWT response for diagnostics (before stripping/extraction)
                try:
                    if getattr(self, 'DUMP_GWT_RESPONSE', False) and current_page < max(0, int(getattr(self, 'DUMP_GWT_RESPONSE_PAGES', 1))):
                        out_dir = Path(getattr(self, 'DUMP_GWT_RESPONSE_DIR', Path('output') / 'gwt_responses'))
                        out_dir.mkdir(parents=True, exist_ok=True)
                        safe_canton = str(getattr(self, 'canton', 'canton') or 'canton').replace('/', '_').replace('\\', '_')
                        out_path = out_dir / f"{safe_canton}_page_{current_page}.txt"
                        out_path.write_text(response.text or '', encoding='utf-8', errors='ignore')
                        logger.info(f"GWT response dump: {out_path}")
                except Exception as exc:
                    try:
                        logger.debug(f"Impossible d'écrire le dump GWT response: {exc}")
                    except Exception:
                        pass

                # Nettoyer la réponse GWT
                content = gwt_utils.strip_gwt_prefix(response.text)
                logger.debug(f"Contenu nettoyé: {content[:500]}")
                
                # Extraire les valeurs (= tokens) à partir de la réponse GWT.
                #
                # Point “piège” pour découvrir GWT-RPC:
                # - La réponse ressemble à une grande liste de valeurs sérialisées.
                # - Beaucoup de valeurs sont dédupliquées via une “string table”.
                #   Concrètement, on peut voir des références numériques qui pointent vers
                #   une valeur déjà présente ailleurs dans le payload.
                # - Notre approche consiste à extraire une liste `werte` de tokens lisibles,
                #   puis à reconstruire les lignes localement à partir des positions de DocIds.
                werte = gwt_utils.extract_tokens(response.text)

                if len(werte) < 9:
                    logger.warning(f"Résultats insuffisants ({len(werte)} valeurs), page ignorée")
                else:
                    # --- Parsing multi-lignes basé sur DocId ---
                    # Une réponse GWT contient les valeurs de toutes les lignes concaténées.
                    # On NE peut pas "split" naïvement par longueur fixe.
                    # On procède donc ainsi:
                    # - détecter tous les DocIds (identifiants de lignes)
                    # - pour chaque DocId, créer une fenêtre locale (ROW_WINDOW_BEFORE/AFTER)
                    # - dans cette fenêtre, sélectionner le DocId attendu (sinon on risque de
                    #   prendre le DocId voisin et de dupliquer/saute des items)
                    yielded_this_page = 0
                    page_pdates = []
                    docid_positions = self._collect_docid_positions(content)
                    id_indexes = self._find_docid_indexes(werte)

                    # Some GWT payloads (notably bern2) can contain many DocId-like strings when the
                    # response is decoded beyond the UI string table. In that case, the raw DocId list
                    # can be very noisy and not ordered like UI rows.
                    #
                    # To make this robust across cantons, we detect the "noisy" case and:
                    # 1) keep only DocIds that have a dossier number nearby (likely a real row)
                    # 2) sort candidates by detected publication date (newest first)
                    # 3) then apply the usual per-page limit.
                    if id_indexes and len(id_indexes) > max(80, int(getattr(self, 'max_ids_per_page', 25)) * 3):
                        try:
                            noisy_before = 80
                            noisy_after = 260
                            candidates = []
                            seen_docids = set()
                            for pos_in_page, idx in enumerate(id_indexes):
                                doc_id = self._safe_get(werte, idx, None)
                                if not doc_id or doc_id in seen_docids:
                                    continue
                                seen_docids.add(doc_id)

                                win_start = max(0, idx - noisy_before)
                                win_end = min(len(werte), idx + noisy_after)

                                next_doc_idx = None
                                try:
                                    if pos_in_page + 1 < len(id_indexes):
                                        next_doc_idx = id_indexes[pos_in_page + 1]
                                except Exception:
                                    next_doc_idx = None
                                row_end_hint = None
                                if isinstance(next_doc_idx, int) and next_doc_idx > win_start:
                                    row_end_hint = next_doc_idx - win_start

                                slice_werte = werte[win_start:win_end]
                                id_hint = idx - win_start

                                # Quick pre-filter: require at least one dossier-number token nearby.
                                has_num_token = False
                                try:
                                    pats = list(getattr(self, '_num_patterns', []) or [])
                                    # In many payloads, dossier numbers can be split across several tokens
                                    # (e.g., "100", "2024", "331"). Try 1..MAX_JOINED_TOKENS concatenations.
                                    for j in range(len(slice_werte)):
                                        if has_num_token:
                                            break
                                        for k in range(1, MAX_JOINED_TOKENS + 1):
                                            if j + k > len(slice_werte):
                                                break
                                            parts = [t for t in slice_werte[j:j + k] if t]
                                            if not parts:
                                                continue
                                            joined = ' '.join(parts).strip()
                                            if not joined:
                                                continue
                                            for pat in pats:
                                                try:
                                                    if pat.fullmatch(joined):
                                                        has_num_token = True
                                                        break
                                                except Exception:
                                                    continue
                                            if has_num_token:
                                                break
                                except Exception:
                                    has_num_token = False
                                if not has_num_token:
                                    continue

                                meta, _docpos = self._extract_row_meta(
                                    slice_werte,
                                    counters=None,
                                    row_end_hint=row_end_hint,
                                    preferred_doc_id=doc_id,
                                    preferred_id_pos=id_hint,
                                )

                                num = (meta.get('Num') or '').strip() if isinstance(meta, dict) else ''

                                # Prefer PDatum, fallback EDatum for sorting.
                                pds = (meta.get('PDatum') or '').strip() if isinstance(meta, dict) else ''
                                eds = (meta.get('EDatum') or '').strip() if isinstance(meta, dict) else ''
                                dt = None
                                try:
                                    if pds:
                                        dt = self._parse_date_any(pds)
                                except Exception:
                                    dt = None
                                if dt is None:
                                    try:
                                        if eds:
                                            dt = self._parse_date_any(eds)
                                    except Exception:
                                        dt = None
                                # Sort: newest first; prefer rows where a Num was detected.
                                dt_key = dt.toordinal() if dt else -1
                                has_num = 1 if num else 0
                                candidates.append((dt_key, has_num, idx, doc_id))

                            # Sort candidates newest-first; stable tie-breaker by appearance index.
                            if candidates:
                                candidates.sort(key=lambda t: (t[0], t[1], -t[2]), reverse=True)
                                filtered = [idx for _dt, _hn, idx, _did in candidates]
                                logger.info(
                                    f"DocIds noisy detected: {len(id_indexes)} -> row candidates: {len(filtered)} (sorted newest-first)"
                                )
                                id_indexes = filtered
                            else:
                                logger.info(
                                    f"DocIds noisy detected: {len(id_indexes)} but 0 row candidates matched (keeping raw list)"
                                )
                        except Exception:
                            pass

                    if not id_indexes:
                        logger.warning("Aucun DocId détecté sur cette page")
                    else:
                        logger.debug(f"DocIds détectés: {len(id_indexes)} (premiers index: {id_indexes[:5]})")

                    # Par heuristique, une page UI affiche ~20 lignes; on limite à 25 pour ne pas rater
                    # des lignes décalées. On log le nombre d'IDs détectés et le nombre d'items yieldés.
                    # Sample first few DocIds for troubleshooting
                    sample_ids = [self._safe_get(werte, i, '') for i in id_indexes[:5]] if id_indexes else []
                    logger.info(f"Page {current_page}: {len(id_indexes)} DocIds détectés (exemples: {sample_ids})")
                    # Noter si l'échantillon semble identique à la page précédente (diagnostic, pas d'arrêt ici)
                    try:
                        if self._prev_docid_sample is not None and sample_ids and self._prev_docid_sample == sample_ids:
                            logger.warning("Échantillon DocIds identique à la page précédente — possible répétition de page")
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
                        'docpos_missing': 0,
                    }

                    # Per-page date counters (for page-fraction early-stop)
                    page_count_with_dates = 0
                    page_count_older = 0
                    eligible_docids = 0
                    page_count_eligible = 0
                    debug_old_logged = 0
                    # Respecter un plafond global d'items si demandé
                    limit_ids = min(len(id_indexes), getattr(self, 'max_ids_per_page', 25))
                    for pos_in_page, idx in enumerate(id_indexes[:limit_ids]):
                        if not self._can_emit_more():
                            logger.info(f"Cap MAX_ITEMS atteint ({self.MAX_ITEMS}), arrêt de l'émission sur cette page")
                            break
                        try:
                            doc_id = self._safe_get(werte, idx, None)
                        except Exception:
                            continue

                        # Always reset per-iteration locals (avoid accidental reuse)
                        meta = None
                        doc_id_pos = None

                        # Fenêtre locale autour du token DocId (index `idx`).
                        # Astuce importante: la fenêtre peut contenir plusieurs DocIds.
                        # On passe donc un "hint" (preferred_doc_id + preferred_id_pos) pour
                        # forcer le choix du bon DocId dans la fenêtre.
                        win_start = max(0, idx - ROW_WINDOW_BEFORE)
                        win_end = min(len(werte), idx + ROW_WINDOW_AFTER)

                        # Row boundary hint (index of next known DocId) to avoid cross-row date bleed.
                        # Also used to optionally extend the window to include the full row.
                        next_doc_idx = None
                        try:
                            if pos_in_page + 1 < len(id_indexes):
                                next_doc_idx = id_indexes[pos_in_page + 1]
                        except Exception:
                            next_doc_idx = None
                        row_end_hint = None
                        if isinstance(next_doc_idx, int) and next_doc_idx > win_start:
                            row_end_hint = next_doc_idx - win_start

                        # If publication date is far after the DocId, the fixed window can miss it.
                        # When the row size is reasonable, extend to the next DocId to capture all row tokens.
                        try:
                            if isinstance(next_doc_idx, int) and next_doc_idx > win_end:
                                row_len = next_doc_idx - win_start
                                if row_len <= 900:
                                    win_end = min(len(werte), next_doc_idx)
                        except Exception:
                            pass

                        slice_werte = werte[win_start:win_end]
                        id_hint = idx - win_start

                        # Déduplication globale par DocId
                        if not doc_id or doc_id in self.seen_ids:
                            drop_counters['dedup_skipped'] += 1
                            continue
                        self.seen_ids.add(doc_id)

                        # Position dans le contenu brut: sert aux fallbacks.
                        # Exemple: certains cantons ont le PDatum loin après le DocId, ou le
                        # pfad PDF n'apparaît pas dans la fenêtre tokenisée.
                        positions = docid_positions.get(doc_id)
                        if positions:
                            doc_id_pos = positions.pop(0)
                            if not positions:
                                docid_positions.pop(doc_id, None)
                        else:
                            drop_counters['docpos_missing'] = drop_counters.get('docpos_missing', 0) + 1

                        # Meta léger (sans requêtes réseau): permet d'appliquer la logique days=N
                        # et d'éviter des decrypts inutiles.
                        meta = self._extract_row_meta(
                            slice_werte,
                            drop_counters,
                            preferred_doc_id=doc_id,
                            preferred_id_pos=id_hint,
                            row_end_hint=row_end_hint,
                            page_content=content,
                            doc_id_pos=doc_id_pos,
                        )

                        # Certains cantons placent PDatum très loin après le DocId (souvent après
                        # de longs textes). Si la fenêtre est trop courte, on peut:
                        # - rater PDatum
                        # - et donc prendre un cutoff_date_obj trop ancien (false "old_item")
                        # => On agrandit la fenêtre de façon opportuniste si PDatum manque ou
                        #    semble < min_date.
                        if meta is not None and self.min_date:
                            pdatum_try = (meta.get('PDatum') or '').strip() if isinstance(meta, dict) else ''
                            pdate_obj_try = None
                            if pdatum_try:
                                try:
                                    pdate_obj_try = self._parse_date_any(pdatum_try)
                                except Exception:
                                    pdate_obj_try = None
                            needs_expand = (not pdatum_try) or (pdate_obj_try is not None and pdate_obj_try < self.min_date)
                            if needs_expand:
                                expanded_end = min(len(werte), idx + max(ROW_WINDOW_AFTER, 180))
                                if expanded_end > win_end:
                                    expanded_slice = werte[win_start:expanded_end]
                                    expanded_meta = self._extract_row_meta(
                                        expanded_slice,
                                        drop_counters,
                                        preferred_doc_id=doc_id,
                                        preferred_id_pos=id_hint,
                                        row_end_hint=(next_doc_idx - win_start) if isinstance(next_doc_idx, int) and next_doc_idx > win_start else None,
                                        page_content=content,
                                        doc_id_pos=doc_id_pos,
                                    )
                                    if expanded_meta is not None:
                                        slice_werte = expanded_slice
                                        meta = expanded_meta
                        if meta is None:
                            # In "all detected eligible" mode, do not silently drop a DocId because
                            # lightweight parsing failed: attempt full row parsing (with an expanded window).
                            if getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', False):
                                eligible_docids += 1
                                parsed = self.parse_result_row(
                                    slice_werte,
                                    content,
                                    page_nr=current_page,
                                    counters=drop_counters,
                                    doc_id_position=doc_id_pos,
                                    preferred_doc_id=doc_id,
                                    preferred_id_pos=id_hint,
                                    row_end_hint=row_end_hint,
                                )
                                if parsed is None:
                                    expanded_end = min(len(werte), idx + max(ROW_WINDOW_AFTER, 260))
                                    expanded_slice = werte[win_start:expanded_end]
                                    parsed = self.parse_result_row(
                                        expanded_slice,
                                        content,
                                        page_nr=current_page,
                                        counters=drop_counters,
                                        doc_id_position=doc_id_pos,
                                        preferred_doc_id=doc_id,
                                        preferred_id_pos=id_hint,
                                        row_end_hint=(next_doc_idx - win_start) if isinstance(next_doc_idx, int) and next_doc_idx > win_start else None,
                                    )
                                    if parsed is not None:
                                        slice_werte = expanded_slice

                                if parsed is None:
                                    logger.warning(f"DocId {doc_id}: parsing échoué (meta None) — DocId non yieldé")
                                    continue

                                # Register this DocId for the per-page barrier.
                                barrier_docid = doc_id
                                try:
                                    if isinstance(parsed, scrapy.Request):
                                        it = parsed.meta.get('item') if hasattr(parsed, 'meta') else None
                                        if isinstance(it, dict) and it.get('DocId'):
                                            barrier_docid = it.get('DocId')
                                    elif isinstance(parsed, dict) and parsed.get('DocId'):
                                        barrier_docid = parsed.get('DocId')
                                except Exception:
                                    barrier_docid = doc_id
                                self._barrier_register(current_page, barrier_docid)

                                if isinstance(parsed, scrapy.Request):
                                    yielded_this_page += 1
                                    yield parsed
                                    continue

                                if isinstance(parsed, dict):
                                    if not self._can_emit_more():
                                        next_req = self._barrier_done(current_page, barrier_docid)
                                        if next_req is not None:
                                            yield next_req
                                        continue
                                    docid = parsed.get('DocId')
                                    if docid and docid in self._yielded_docids:
                                        next_req = self._barrier_done(current_page, barrier_docid)
                                        if next_req is not None:
                                            yield next_req
                                        continue
                                    if docid:
                                        self._yielded_docids.add(docid)
                                    self.items_emitted += 1
                                    try:
                                        self.crawler.stats.inc_value('tribuna/items_emitted')
                                    except Exception:
                                        pass
                                    yielded_this_page += 1
                                    yield parsed
                                    next_req = self._barrier_done(current_page, barrier_docid)
                                    if next_req is not None:
                                        yield next_req
                                    continue
                            else:
                                continue

                        # Cutoff date selection for --days:
                        # Prefer publication date, but if it's older than min_date while the decision date is
                        # within range, use the decision date to avoid false "old_item" drops.
                        pdatum_str = (meta.get('PDatum') or '').strip() if isinstance(meta, dict) else ''
                        edatum_str = (meta.get('EDatum') or '').strip() if isinstance(meta, dict) else ''
                        pdatum_obj = None
                        edatum_obj = None
                        if pdatum_str:
                            try:
                                pdatum_obj = self._parse_date_any(pdatum_str)
                            except Exception:
                                pdatum_obj = None
                        if edatum_str:
                            try:
                                edatum_obj = self._parse_date_any(edatum_str)
                            except Exception:
                                edatum_obj = None

                        cutoff_date_obj = pdatum_obj or edatum_obj
                        if self.min_date and pdatum_obj and edatum_obj:
                            if pdatum_obj < self.min_date <= edatum_obj:
                                cutoff_date_obj = edatum_obj

                        if cutoff_date_obj:
                            page_count_with_dates += 1
                            page_pdates.append(cutoff_date_obj)

                        # Eligibility in --days: anything within [min_date, max_date] is eligible.
                        is_eligible = True
                        if self.min_date and cutoff_date_obj and cutoff_date_obj < self.min_date:
                            is_eligible = False
                        if getattr(self, 'max_date', None) and cutoff_date_obj and cutoff_date_obj > self.max_date:
                            is_eligible = False
                        if self.min_date and cutoff_date_obj is None and (not getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', False)):
                            is_eligible = False

                        # --- Règle days=N (éligibilité / cutoff) ---
                        # On applique le cutoff *avant* de lancer des decrypts/GET PDFs.
                        # Sinon on gaspille beaucoup de requêtes pour des items hors-plage.
                        #
                        # Deux comportements:
                        # - Server-side filter actif (REQUIRE_ALL_DETECTED_ELIGIBLE=True):
                        #   tout DocId détecté est éligible (on ne drop pas localement par date).
                        # - Sinon: on filtre localement (cutoff_date_obj < min_date ou > max_date).
                        if getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', False):
                            eligible_docids += 1
                        if (not getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', False)) and (not is_eligible):
                            drop_counters['old_item'] = drop_counters.get('old_item', 0) + 1
                            if self.min_date and cutoff_date_obj and cutoff_date_obj < self.min_date:
                                page_count_older += 1
                            # Debug ciblé: bern2 est le seul canton où l'UI semble montrer davantage
                            # d'items pour une même date de publication; logguer quelques drops.
                            try:
                                if (
                                    debug_old_logged < 8
                                    and str(getattr(self, 'canton', '')).lower() == 'bern2'
                                    and int(current_page) == 0
                                    and getattr(self, 'min_date', None) is not None
                                ):
                                    debug_old_logged += 1
                                    logger.info(
                                        "bern2 debug drop(old_item): DocId=%s Num=%s PDatum=%s EDatum=%s cutoff=%s min_date=%s"
                                        % (
                                            doc_id,
                                            (meta.get('Num') if isinstance(meta, dict) else None),
                                            pdatum_str or None,
                                            edatum_str or None,
                                            (cutoff_date_obj.isoformat() if cutoff_date_obj else None),
                                            (self.min_date.isoformat() if self.min_date else None),
                                        )
                                    )
                            except Exception:
                                pass
                            # Do not issue decrypt for this row; skip emitting
                            continue
                        if not getattr(self, 'REQUIRE_ALL_DETECTED_ELIGIBLE', False):
                            eligible_docids += 1
                            page_count_eligible += 1

                        # Otherwise perform full parsing (which may issue decrypt request)
                        parsed = self.parse_result_row(
                            slice_werte,
                            content,
                            page_nr=current_page,
                            counters=drop_counters,
                            doc_id_position=doc_id_pos,
                            preferred_doc_id=doc_id,
                            preferred_id_pos=id_hint,
                            row_end_hint=row_end_hint,
                        )

                        # parse_result_row peut retourner: None | Request | Item
                        if parsed is None:
                            continue

                        # Register this DocId for the per-page barrier (prefer the parsed DocId).
                        barrier_docid = doc_id
                        try:
                            if isinstance(parsed, scrapy.Request):
                                it = parsed.meta.get('item') if hasattr(parsed, 'meta') else None
                                if isinstance(it, dict) and it.get('DocId'):
                                    barrier_docid = it.get('DocId')
                            elif isinstance(parsed, dict) and parsed.get('DocId'):
                                barrier_docid = parsed.get('DocId')
                        except Exception:
                            barrier_docid = doc_id
                        self._barrier_register(current_page, barrier_docid)

                        if isinstance(parsed, scrapy.Request):
                            yielded_this_page += 1
                            yield parsed
                            continue

                        if isinstance(parsed, dict):
                            if not self._can_emit_more():
                                # Unblock barrier even if we cannot emit more
                                next_req = self._barrier_done(current_page, barrier_docid)
                                if next_req is not None:
                                    yield next_req
                                continue

                            docid = parsed.get('DocId')
                            if docid and docid in self._yielded_docids:
                                next_req = self._barrier_done(current_page, barrier_docid)
                                if next_req is not None:
                                    yield next_req
                                continue
                            if docid:
                                self._yielded_docids.add(docid)

                            # collect PDatum if available
                            try:
                                p = parsed.get('PDatum')
                                if p:
                                    try:
                                        pd = self._parse_date_any(p)
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

                            # Immediate terminal (no decrypt) — mark as done
                            next_req = self._barrier_done(current_page, barrier_docid)
                            if next_req is not None:
                                yield next_req

                    if yielded_this_page == 0:
                        logger.info("Aucune ligne exploitable trouvée sur la page (après déduplication)")
                    else:
                        logger.info(
                            f"Page {current_page}: {eligible_docids} DocIds éligibles (après déduplication/filtre --days)"
                        )
                        logger.info(f"Page {current_page}: {yielded_this_page} items yieldés")

                        if eligible_docids != yielded_this_page:
                            logger.warning(
                                f"Mismatch éligibles vs yieldés sur la page {current_page}: eligible={eligible_docids} yielded={yielded_this_page}"
                            )

                    # Log drop reasons for this page
                    # Push drop counters into Scrapy stats
                    try:
                        for k, v in drop_counters.items():
                            if v:
                                self.crawler.stats.inc_value(f'tribuna/drops/{k}', v)
                    except Exception:
                        pass
                    logger.info(f"Page {current_page} drops: id_missing={drop_counters['id_missing']}, num_missing={drop_counters['num_missing']}, pfad_missing={drop_counters['pfad_missing']}, decrypt_failed={drop_counters['decrypt_failed']}, edatum_missing={drop_counters['edatum_missing']}, old_item={drop_counters.get('old_item', 0)}, dedup_skipped={drop_counters.get('dedup_skipped', 0)}, docpos_missing={drop_counters.get('docpos_missing', 0)}")
                    # Diagnostics: min/max PDatum sur la page
                    try:
                        if page_pdates:
                            page_min = min(page_pdates)
                            page_max = max(page_pdates)
                            samples = [d.isoformat() for d in sorted(page_pdates, reverse=True)[:3]]
                            logger.info(f"Page {current_page} PDatum range: min={page_min.isoformat()} max={page_max.isoformat()} samples={samples}")
                            # Record recent minima for monotonicity check (first pages)
                            try:
                                if len(self._recent_page_mins) >= 3:
                                    self._recent_page_mins.pop(0)
                                self._recent_page_mins.append(page_min)
                                if len(self._recent_page_mins) >= 2:
                                    # If monotonic non-increasing violated, warn
                                    if any(self._recent_page_mins[i] < self._recent_page_mins[i+1] for i in range(len(self._recent_page_mins)-1)):
                                        logger.warning("Server-side publication sort does not appear monotonic across sampled pages")
                            except Exception:
                                pass
                            # Page-fraction early-stop when sorted by publication: require a threshold fraction of dated items
                            # to be older than min_date to trigger early-stop.
                            # Note: we apply this even in STRICT_FULL for --days runs; STRICT_FULL should primarily
                            # control PDF/decrypt completeness, not disable incremental cutoff.
                            if getattr(self, 'sort', None) == 'publication' and self.min_date and page_count_with_dates > 0:
                                try:
                                    frac_old = float(page_count_older) / float(page_count_with_dates)
                                except Exception:
                                    frac_old = 0.0
                                logger.info(f"Page {current_page} older fraction: {frac_old:.2f} (threshold={self.PAGE_OLDER_FRACTION:.2f})")
                                # Important nuance: some instances (notably bern2/StRK) can return pages where older and
                                # newer publication dates are mixed even under the UI "tri par date".
                                # In that case, stopping as soon as we see ONE older item will miss newer items that
                                # appear on following pages.
                                #
                                # Robust rule for incremental runs: only stop once an entire page is beyond the cutoff
                                # (i.e. its MAX publication date is < min_date). As an optimization, we also allow the
                                # fraction-based early-stop but only after the first page.
                                if getattr(self, 'days', None) is not None:
                                    try:
                                        if page_pdates and max(page_pdates) < self.min_date:
                                            logger.info(
                                                f"Arrêt: max(PDatum)={max(page_pdates).isoformat()} < min_date={self.min_date.isoformat()} (mode --days)"
                                            )
                                            self._older_seen = True
                                        elif current_page > 0 and frac_old >= float(self.PAGE_OLDER_FRACTION):
                                            self._older_seen = True
                                    except Exception:
                                        pass
                                else:
                                    if frac_old >= float(self.PAGE_OLDER_FRACTION):
                                        self._older_seen = True
                                # If any dated item exceeds max_date, also stop immediately
                                if getattr(self, 'days', None) is not None and getattr(self, 'max_date', None):
                                    if any(d > self.max_date for d in page_pdates if d):
                                        logger.info("Arrêt immédiat: élément au-delà de la borne haute (today) trouvé sur la page (mode --days)")
                                        self._older_seen = True
                    except Exception:
                        pass
                    # Mémoriser l'échantillon DocIds courant pour comparaison avec la page suivante
                    try:
                        self._prev_docid_sample = list(sample_ids) if sample_ids else None
                    except Exception:
                        self._prev_docid_sample = None

                    # Stop rule for --days + publication sort: stop when there is no eligible item on the page.
                    try:
                        if self.min_date and (getattr(self, 'sort', None) == 'publication') and page_count_eligible == 0:
                            logger.info(
                                f"Arrêt: 0 item éligible sur la page {current_page} (mode --days + tri publication)"
                            )
                            self._older_seen = True
                    except Exception:
                        pass
            else:
                logger.warning("0 résultats trouvés")
                return
            
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

            # Arrêt strict dès que tous les DocIds annoncés ont été traités
            try:
                if should_continue and self.trefferzahl and len(self.seen_ids) >= self.trefferzahl:
                    logger.info(
                        f"Arrêt pagination: {len(self.seen_ids)} DocIds >= trefferzahl ({self.trefferzahl})"
                    )
                    should_continue = False
            except Exception:
                pass

            # Arrêt anticipé si l'échantillon DocIds n'a pas changé par rapport à la page précédente
            # Note: la comparaison est faite plus haut et stockée dans similar_to_prev
            try:
                if should_continue and not getattr(self, 'STRICT_FULL', False):
                    if 'similar_to_prev' in locals() and similar_to_prev and current_page > 0:
                        logger.warning("Arrêt pagination: pages consécutives semblent identiques (échantillon DocIds)")
                        should_continue = False
            except Exception:
                pass

            # Early-stop when sorted by publication date and older items seen
            try:
                if should_continue:
                    if getattr(self, 'sort', None) == 'publication' and self._older_seen:
                        logger.info("Arrêt pagination: items plus anciens que min_date rencontrés sous tri publication")
                        should_continue = False
            except Exception:
                pass

            if not stop_due_to_date and should_continue:
                body = self.get_next_request()
                # nouveau numéro de page pour la requête suivante
                next_page = self.page_nr - 1
                next_req = scrapy.Request(
                    url=self.RESULT_PAGE_URL,
                    method="POST",
                    body=body,
                    headers=self.HEADERS,
                    callback=self.parse_page,
                    errback=self.errback_httpbin,
                    meta={'page_nr': next_page},
                    dont_filter=True
                )
                if getattr(self, 'PAGE_PDF_BARRIER', False):
                    pending = self._page_pending_docids.get(current_page)
                    if pending and len(pending) > 0:
                        self._page_next_request[current_page] = next_req
                        logger.info(
                            f"Barrier active: attente PDFs page {current_page} (pending={len(pending)}), pagination différée"
                        )
                    else:
                        yield next_req
                else:
                    yield next_req
            else:
                logger.info(f"Fin du scraping: {self.pages_processed} pages traitées (MAX_PAGES={self.MAX_PAGES})")
        else:
            logger.error(f"Réponse invalide: status={response.status}, len={len(response.body)}")
            # Dernier recours: tenter un re-bootstrap si non fait
            if self._bootstrap_retry_count < self.MAX_BOOTSTRAP_RETRIES:
                self._bootstrap_retry_count += 1
                logger.warning(
                    f"Réponse invalide; re-bootstrap {self._bootstrap_retry_count}/{self.MAX_BOOTSTRAP_RETRIES}"
                )
                bootstrap_url = self.config.get('bootstrap_url') or self.config.get('base_url', self.RESULT_PAGE_URL)
                self.page_nr = current_page
                yield scrapy.Request(
                    url=bootstrap_url,
                    method="GET",
                    headers=self.HEADERS,
                    callback=self.parse_bootstrap,
                    errback=self.errback_httpbin,
                    meta={'referrer_policy': "no-referrer", 'bootstrap_retry': True},
                    dont_filter=True
                )
                return
        return
    
    def parse_result_row(
        self,
        werte,
        content,
        page_nr=None,
        counters=None,
        doc_id_position: Optional[int] = None,
        *,
        preferred_doc_id: Optional[str] = None,
        preferred_id_pos: Optional[int] = None,
        row_end_hint: Optional[int] = None,
    ):
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

            # Sélectionner le DocId correct dans la fenêtre.
            # C'est un point critique: si la fenêtre contient plusieurs DocIds et qu'on prend
            # le premier, on va:
            # - dupliquer un DocId déjà traité
            # - et rater le DocId attendu
            id_, id_pos = self._select_docid_in_window(werte, preferred_doc_id, preferred_id_pos)
            if id_pos is None or not id_:
                # increment id_missing counter if provided
                if counters is not None and isinstance(counters, dict):
                    counters['id_missing'] += 1
                logger.debug(f"ID non trouvé dans fenêtre locale")
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
                logger.warning(f"Titre trop court: '{titel}'")
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

            # Pfad PDF: selon le canton, on a soit:
            # - un chemin Windows vers un PDF (ex: "X:\...\file.pdf")
            # - une chaîne hex longue (nouvelle syntaxe) qui doit être décryptée
            pfad, neuePfadsyntax = self._detect_pdf_path(
                werte,
                counters,
                doc_id=id_,
                page_content=content,
                doc_id_pos=doc_id_position,
            )

            if not pfad:
                snippet_tokens = ' '.join(t for t in werte[max(0, id_pos - 4):id_pos + 4] if t)
                logger.warning(
                    f"DocID {id_} ne contient pas de chemin PDF identifié (tokens autour de l'ID: {snippet_tokens})"
                )

            # Dates:
            # - EDatum = date de décision
            # - PDatum = date de publication (souvent utilisée pour l'incrémental)
            # La détection est volontairement "row-local" pour éviter de prendre une date
            # d'une autre ligne et de fausser le cutoff.
            entscheiddatum, publikationsdatum = self._detect_dates(
                werte,
                id_pos,
                neuePfadsyntax,
                counters,
                row_end_hint=row_end_hint,
                doc_id=id_,
                page_content=content,
                doc_id_pos=doc_id_position,
            )
            
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
            item['Signatur'] = ''
            item['Gericht'] = ''
            item['Kammer'] = ''
            item['VGericht'] = ''
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
            item['HTMLUrls'] = []
            item['PDFUrls'] = []
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
            
            # --- Gestion du PDF ---
            # Deux modes:
            # 1) ENCRYPTED=True: on doit POST sur DECRYPT_PAGE_URL pour obtenir un token/URL
            #    puis tester une liste d'URLs candidates (selon les variantes Tribuna).
            # 2) ENCRYPTED=False: on construit directement une URL PDF et on la met dans item['PDFUrls'].
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

                # Requête de décryptage: renvoie une réponse GWT qui contient (souvent)
                # un "partURL" / token permettant de construire les endpoints PDF.
                body = self.DECRYPT_START + pfad_encrypt + self.DECRYPT_END

                return scrapy.Request(
                    url=self.DECRYPT_PAGE_URL,
                    method="POST",
                    body=body,
                    headers=self.HEADERS,
                    callback=self.decrypt_path,
                    errback=self.errback_decrypt,
                    meta={"item": item, 'page_nr': item.get('PageNr'), 'counters': counters},
                    dont_filter=True
                )
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
            logger.error(f"Erreur parsing résultat: {e}", exc_info=True)
            return None
    
    async def decrypt_path(self, response):
        """Callback decrypt: construit des URLs PDF candidates, puis vérifie la première.

        Étapes:
        - Parser la réponse GWT de décryptage (souvent //OK[...])
        - Construire une liste de candidates via `_build_pdf_candidates_from_decrypt`
        - Lancer un GET sur la première candidate
        - Le callback `verify_pdf_candidate` validera le PDF (status/content-type)
          ou enchaînera sur les candidates suivantes.

        Règle importante: quelle que soit l'issue (succès PDF ou échec final),
        on yield un item (au pire sans PDF) et on appelle `_barrier_done`.
        """
        item = response.meta['item']
        page_nr = response.meta.get('page_nr', self.page_nr - 1)
        counters = response.meta.get('counters')

        logger.info(f"Décryptage PDF pour DocID {item['DocId']} (page {page_nr})")
        
        if response.status == 200:
            logger.debug(f"Réponse decrypt: {response.text[:200]}")
            # Dump brut de la réponse de décryptage pour analyse (facile à consulter)
            try:
                out_dir = Path(__file__).parent.parent / 'output' / 'debug_decrypts'
                out_dir.mkdir(parents=True, exist_ok=True)
                fname = out_dir / f"decrypt_{item['DocId']}.txt"
                with open(fname, 'w', encoding='utf-8') as fh:
                    fh.write(response.text)
                logger.info(f"Réponse decrypt sauvegardée: {fname}")
            except Exception as _e:
                logger.warning(f"Impossible de sauvegarder réponse decrypt: {_e}")
            
            # Construire les candidats PDF via helper
            built = self._build_pdf_candidates_from_decrypt(response.text, item)
            if built:
                first, candidates = built
                logger.debug(f"Premier candidat PDF: {first}")
                # include GWT headers and allow non-200 responses to be handled in callback
                hdrs = dict(self.HEADERS)
                hdrs.update({'Referer': self.HEADERS.get('Referer', '')})
                yield scrapy.Request(
                    url=first,
                    method="GET",
                    headers=hdrs,
                    callback=self.verify_pdf_candidate,
                    errback=self.errback_pdf_candidate,
                    meta={
                        "item": item,
                        "candidates": candidates,
                        'handle_httpstatus_list': self.PDF_HTTPSTATUS_RETRY,
                        'page_nr': page_nr,
                    },
                    dont_filter=True
                )
                return

            logger.error(f"Impossible de décrypter le PDF pour {item['DocId']}")
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
            try:
                docid = item.get('DocId')
                next_req = self._barrier_done(page_nr, docid)
                if next_req is not None:
                    yield next_req
            except Exception:
                pass
        else:
            logger.error(f"Erreur décryptage: status={response.status}")
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
            try:
                docid = item.get('DocId')
                next_req = self._barrier_done(page_nr, docid)
                if next_req is not None:
                    yield next_req
            except Exception:
                pass
        return

    async def verify_pdf_candidate(self, response):
        """Vérifie si la réponse HTTP est bien un PDF.

        Pourquoi on vérifie:
        - Certaines URLs candidates renvoient un HTML d'erreur, un redirect, ou du JSON.
        - On accepte typiquement 200 (OK) et 206 (Partial Content) pour les PDFs.
        - On valide aussi le Content-Type (application/pdf) via `gwt_utils.is_pdf_ok`.

        Si la candidate échoue:
        - on tente la suivante (s'il reste des candidates)
        - sinon on yield l'item sans PDF (mais avec les métadonnées)
        - dans tous les cas, on débloque la barrière de la page (`_barrier_done`).
        """
        item = response.meta.get('item')
        candidates = response.meta.get('candidates', [])
        page_nr = response.meta.get('page_nr', self.page_nr - 1)

        # Save debug dump for this candidate response (status, headers, small body)
        try:
            out_dir = Path(__file__).parent.parent / 'output' / 'debug_pdf_responses'
            out_dir.mkdir(parents=True, exist_ok=True)
            docid = (item.get('DocId') if item else 'unknown')
            ts = str(int(time.time() * 1000))
            fname = out_dir / f"pdfresp_{docid}_{ts}.txt"
            with open(fname, 'w', encoding='utf-8', errors='ignore') as fh:
                fh.write(f"URL: {response.url}\n")
                fh.write(f"Status: {getattr(response, 'status', None)}\n")
                fh.write("Headers:\n")
                for k, v in response.headers.items():
                    try:
                        fh.write(f"  {k.decode('utf-8',errors='ignore') if isinstance(k,bytes) else k}: {v.decode('utf-8',errors='ignore') if isinstance(v,bytes) else v}\n")
                    except Exception:
                        fh.write(f"  {k}: {v}\n")
                fh.write('\nBody snippet:\n')
                try:
                    snippet = response.text[:2000]
                except Exception:
                    try:
                        snippet = response.body[:2000].decode('utf-8', errors='ignore')
                    except Exception:
                        snippet = '<binary>'
                fh.write(snippet)
            logger.debug(f"Saved PDF candidate debug: {fname}")
        except Exception:
            pass

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
            logger.info(f"PDF trouvé: {response.url} (content-type={ct})")
            try:
                self.items_emitted += 1
                try:
                    self.crawler.stats.inc_value('tribuna/items_emitted')
                except Exception:
                    pass
            except Exception:
                pass
            yield item
            try:
                docid = item.get('DocId')
                next_req = self._barrier_done(page_nr, docid)
                if next_req is not None:
                    yield next_req
            except Exception:
                pass
            return

        # Sinon, essayer le candidat suivant s'il existe
        if candidates:
            next_url = candidates.pop(0)
            logger.debug(f"Candidat PDF suivant: {next_url}")
            hdrs = dict(self.HEADERS)
            hdrs.update({'Referer': self.HEADERS.get('Referer', '')})
            yield scrapy.Request(
                url=next_url,
                method="GET",
                headers=hdrs,
                callback=self.verify_pdf_candidate,
                errback=self.errback_pdf_candidate,
                meta={
                    "item": item,
                    "candidates": candidates,
                    'handle_httpstatus_list': self.PDF_HTTPSTATUS_RETRY,
                    'page_nr': page_nr,
                },
                dont_filter=True
            )
            return

        # Aucun candidat valide
        logger.warning(f"Aucun endpoint PDF valide trouvé pour {item['DocId']}")
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
        try:
            docid = item.get('DocId')
            next_req = self._barrier_done(page_nr, docid)
            if next_req is not None:
                yield next_req
        except Exception:
            pass
        return

    async def errback_decrypt(self, failure):
        """Errback pour la requête de décryptage: émet l'item sans PDF et débloque le barrier."""
        req = getattr(failure, 'request', None)
        meta = getattr(req, 'meta', {}) if req is not None else {}
        item = meta.get('item')
        page_nr = meta.get('page_nr', self.page_nr - 1)
        counters = meta.get('counters')
        self._log_failure(failure, "Erreur requête decrypt")
        if item is None:
            return
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
            try:
                self.crawler.stats.inc_value('tribuna/items_emitted')
            except Exception:
                pass
        except Exception:
            pass
        yield item
        try:
            docid = item.get('DocId')
            next_req = self._barrier_done(page_nr, docid)
            if next_req is not None:
                yield next_req
        except Exception:
            pass
        return

    async def errback_pdf_candidate(self, failure):
        """Errback pour GET PDF candidate: tente le suivant, sinon émet l'item sans PDF et débloque le barrier."""
        req = getattr(failure, 'request', None)
        meta = getattr(req, 'meta', {}) if req is not None else {}
        item = meta.get('item')
        candidates = meta.get('candidates', []) or []
        page_nr = meta.get('page_nr', self.page_nr - 1)
        self._log_failure(failure, "Erreur requête PDF candidate")
        if item is None:
            return

        if candidates:
            next_url = candidates.pop(0)
            hdrs = dict(self.HEADERS)
            hdrs.update({'Referer': self.HEADERS.get('Referer', '')})
            yield scrapy.Request(
                url=next_url,
                method="GET",
                headers=hdrs,
                callback=self.verify_pdf_candidate,
                errback=self.errback_pdf_candidate,
                meta={
                    "item": item,
                    "candidates": candidates,
                    'handle_httpstatus_list': self.PDF_HTTPSTATUS_RETRY,
                    'page_nr': page_nr,
                },
                dont_filter=True
            )
            return

        if not self._can_emit_more():
            return
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
        try:
            next_req = self._barrier_done(page_nr, docid)
            if next_req is not None:
                yield next_req
        except Exception:
            pass
        return
    
    async def errback_httpbin(self, failure):
        """Gestion des erreurs HTTP"""
        self._log_failure(failure, "Erreur requête")
        return None
