"""GWT-RPC Utilities - Parseur et extracteur pour les réponses GWT.

Ce module fournit les outils pour:
- Parser les réponses GWT-RPC (format //OK[...])
- Extraire les tokens (string table) du payload
- Localiser les DocIds et leurs positions
- Construire les URLs candidates pour le téléchargement PDF
- Vérifier la validité des réponses PDF

Les réponses GWT utilisent un format spécifique:
- Préfixe //OK suivi d'un tableau JavaScript-like
- "String table": tableau de chaînes référencées par index
- Les données sont des références numériques vers la string table

Usage:
    from publication_scraper.spiders import gwt_utils
    
    # Extraire les tokens d'une réponse
    tokens = gwt_utils.extract_tokens(response.text)
    
    # Trouver les DocIds
    id_indexes = gwt_utils.find_docid_indexes(tokens, RE_ID)
    
    # Vérifier si une réponse est un PDF
    if gwt_utils.is_pdf_ok(response):
        ...
"""

import logging
import re
from typing import Any, List, Optional, Tuple
from urllib.parse import quote, urlencode, urljoin

from scrapy.http import Response

logger = logging.getLogger(__name__)

# ============================================================================
# REGEX PATTERNS POUR PARSING GWT
# ============================================================================

# Nettoyage du préfixe GWT (//OK[123,[...)
RE_VOR = re.compile(r'//OK\[[0-9,\.]+\[')
# Extraction des tokens entre guillemets
RE_ALL = re.compile(r'(?<=",")[^"]*(?:\\"[^"]*)*(?=",)')

# Patterns de validation
RE_DOCID = re.compile(r'^[0-9a-f]{32}$', re.I)  # DocId: 32 hex chars
RE_DOSSIER_GENERIC = re.compile(r'^\d{1,4}\s+\d{4}\s+\d{1,6}$')  # Numéro dossier
RE_ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # Date ISO

# Regex pour extraction de données spécifiques
RE_TREFFER = re.compile(r'(?<=^//OK\[)[0-9]+')  # Trefferzahl (nombre de résultats)
RE_DECRYPT = re.compile(r'(?<=//OK\[1,\[")[0-9a-f]+')  # Token decrypt simple
RE_DECRYPT2 = re.compile(
    r'(?<=//OK)([0-9,"a-z.A-Z/\[\]]+partURL\",\")'
    r'(\?P<p1>[^\"]+_)'
    r'(\?P<p2>[^\"_]+)\",\"'
    r'(\?P<p3>dossiernummer)\",\"'
    r'(\?P<p4>[^\"]+)'
)
# Patterns pour détection de chemins PDF
RE_PFAD = re.compile(r'[A-Z]:(?:\\.+)+\.pdf')  # Chemin Windows (C:\...\file.pdf)
RE_PFAD2 = re.compile(r'[0-9a-f]{128,192}')    # Hash long (nouvelle syntaxe)
RE_DECODE = re.compile(r'\\x([0-9A-Fa-f]{2})')  # Séquences d'échappement


class GwtRpcParseError(Exception):
    pass


def extract_trefferzahl(payload: str) -> Optional[int]:
    """Extract the total result count (Trefferzahl) from a raw GWT //OK payload.

    Many Tribuna endpoints prefix their response with something like:
        //OK[123,[ ...

    This helper is intentionally tolerant: it does not require full parsing of the
    payload and returns None when the count can't be found.
    """
    text = (payload or '').lstrip()
    if not text.startswith('//OK['):
        # Sometimes there is whitespace before //OK
        ok = text.find('//OK[')
        if ok < 0:
            return None
        text = text[ok:]
    # Capture the first integer right after //OK[
    m = re.search(r'^//OK\[(\d+)', text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def extract_bootstrap_tokens(
    html: str,
    *,
    base_url: str,
    response: Optional[Response] = None,
) -> tuple[Optional[str], Optional[str], List[str]]:
    """Extract GWT bootstrap tokens from an application HTML page.

    Returns:
    - permutation ("strongName" / X-GWT-Permutation)
    - module_base (X-GWT-Module-Base)
    - nocache script URLs (absolute), for optional follow-up requests

    Notes:
    - `response.urljoin()` is preferred when provided (Scrapy canonical joining).
    - This function is pure extraction; callers decide what to request next.
    """
    html = html or ''
    base_url = base_url or ''

    found_perm: Optional[str] = None
    found_module: Optional[str] = None

    try:
        cache_scripts = re.findall(r'src=["\']([^"\']+\.cache\.js)["\']', html) or []
    except Exception:
        cache_scripts = []
    try:
        nocache_scripts = re.findall(r'src=["\']([^"\']+\.nocache\.js)["\']', html) or []
    except Exception:
        nocache_scripts = []

    try:
        inline_sm = re.search(r'strongName\s*[:=]\s*[\"\']([A-Za-z0-9._-]{8,})[\"\']', html)
    except Exception:
        inline_sm = None
    if inline_sm:
        found_perm = inline_sm.group(1)

    def _url_join(path_or_url: str) -> Optional[str]:
        try:
            if response is not None:
                return response.urljoin(path_or_url)
        except Exception:
            pass
        try:
            return urljoin(base_url, path_or_url)
        except Exception:
            return None

    def _module_from_src(src_url: str) -> Optional[str]:
        try:
            base_part = src_url.rsplit('/', 1)[0] + '/'
        except Exception:
            return None
        return _url_join(base_part)

    # Prefer a direct .cache.js hit
    if cache_scripts and not found_perm:
        for src in cache_scripts:
            try:
                perm_match = re.search(r'([A-Za-z0-9._-]{8,})\.cache\.js', src)
            except Exception:
                perm_match = None
            if perm_match:
                found_perm = perm_match.group(1)
                found_module = _module_from_src(src)
                break

    if found_module is None and cache_scripts:
        found_module = _module_from_src(cache_scripts[0])

    nocache_abs: List[str] = []
    for nc in nocache_scripts:
        u = _url_join(nc)
        if u:
            nocache_abs.append(u)

    return found_perm, found_module, nocache_abs


def strip_gwt_prefix(payload: str) -> str:
    """Remove the GWT OK prefix to make token extraction easier."""
    return RE_VOR.sub('', payload or '')


def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] in (' ', '\t', '\r', '\n'):
        i += 1
    return i


def _parse_string(text: str, i: int) -> tuple[str, int]:
    # Expects opening quote at text[i] == '"'
    i += 1
    out: List[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            return ''.join(out), i + 1
        if ch == '\\':
            i += 1
            if i >= n:
                break
            esc = text[i]
            if esc == 'n':
                out.append('\n')
            elif esc == 'r':
                out.append('\r')
            elif esc == 't':
                out.append('\t')
            elif esc == 'b':
                out.append('\b')
            elif esc == 'f':
                out.append('\f')
            elif esc == '"':
                out.append('"')
            elif esc == '\\':
                out.append('\\')
            elif esc == '/':
                out.append('/')
            elif esc == 'u' and i + 4 < n:
                hx = text[i + 1:i + 5]
                try:
                    out.append(chr(int(hx, 16)))
                    i += 4
                except Exception:
                    # Keep literally when invalid
                    out.append('u' + hx)
                    i += 4
            else:
                # Tolerant: keep unknown escapes as the escaped char.
                out.append(esc)
            i += 1
            continue
        out.append(ch)
        i += 1
    raise GwtRpcParseError('Unterminated string')


def _parse_number(text: str, i: int) -> tuple[Any, int]:
    n = len(text)
    j = i
    if j < n and text[j] == '-':
        j += 1
    has_dot = False
    while j < n:
        c = text[j]
        if c.isdigit():
            j += 1
            continue
        if c == '.' and not has_dot:
            has_dot = True
            j += 1
            continue
        break
    if j == i or (j == i + 1 and text[i] == '-'):
        raise GwtRpcParseError('Invalid number')
    raw = text[i:j]
    try:
        if has_dot:
            return float(raw), j
        return int(raw), j
    except Exception as exc:
        raise GwtRpcParseError(f'Invalid number: {raw}') from exc


def _parse_value(text: str, i: int) -> tuple[Any, int]:
    i = _skip_ws(text, i)
    if i >= len(text):
        raise GwtRpcParseError('Unexpected end')
    ch = text[i]
    if ch == '[':
        i += 1
        arr: List[Any] = []
        i = _skip_ws(text, i)
        if i < len(text) and text[i] == ']':
            return arr, i + 1
        while True:
            val, i = _parse_value(text, i)
            arr.append(val)
            i = _skip_ws(text, i)
            if i >= len(text):
                raise GwtRpcParseError('Unterminated array')
            if text[i] == ',':
                i += 1
                continue
            if text[i] == ']':
                return arr, i + 1
            raise GwtRpcParseError(f'Unexpected char in array: {text[i]}')
    if ch == '"':
        return _parse_string(text, i)
    if ch.isdigit() or ch == '-':
        return _parse_number(text, i)
    # GWT payloads we see here should only contain arrays, numbers, strings.
    raise GwtRpcParseError(f'Unexpected token start: {ch}')


def parse_ok_array(payload: str) -> List[Any]:
    """Parse a GWT-RPC response starting with //OK[...].

    The payload is JavaScript-like (arrays/numbers/strings) and is not always strict JSON.
    This parser is tolerant to unknown backslash escapes.
    """
    text = (payload or '').strip()
    ok_pos = text.find('//OK')
    if ok_pos < 0:
        raise GwtRpcParseError('Missing //OK prefix')
    # Find the first '[' after //OK
    start = text.find('[', ok_pos)
    if start < 0:
        raise GwtRpcParseError('Missing opening [')
    val, end = _parse_value(text, start)
    if not isinstance(val, list):
        raise GwtRpcParseError('Top-level is not an array')
    return val


def _find_string_table(obj: Any) -> Optional[List[str]]:
    """Heuristic: locate the string table array in the parsed GWT payload."""
    if isinstance(obj, list):
        # Candidate: long list of strings
        if len(obj) >= 20 and all(isinstance(x, str) for x in obj):
            return obj  # type: ignore[return-value]
        for it in obj:
            st = _find_string_table(it)
            if st is not None:
                return st
    return None


def extract_string_table(payload: str) -> Optional[List[str]]:
    """Extract the string table from a raw GWT //OK payload.

    Public wrapper around parse_ok_array + _find_string_table.
    Returns the string table (list of unique strings) or None.
    """
    try:
        parsed = parse_ok_array(payload)
        return _find_string_table(parsed)
    except (GwtRpcParseError, Exception) as exc:
        logger.debug("extract_string_table failed: %s", exc)
        return None


def _inflate_tokens(parsed: List[Any], string_table: List[str], max_tokens: int = 200_000) -> List[str]:
    out: List[str] = []

    def walk(x: Any) -> None:
        if len(out) >= max_tokens:
            return
        if isinstance(x, int) and 0 <= x < len(string_table):
            out.append(string_table[x])
            return
        if isinstance(x, str):
            out.append(x)
            return
        if isinstance(x, list):
            for y in x:
                walk(y)

    # Walk everything except the string table itself to preserve locality from references.
    for el in parsed:
        if el is string_table:
            continue
        walk(el)
        if len(out) >= max_tokens:
            break

    return out


def _resolve_atom(atom: Any, string_table: List[str]) -> Optional[str]:
    if isinstance(atom, str):
        return atom
    if isinstance(atom, int) and 0 <= atom < len(string_table):
        return string_table[atom]
    return None


def _score_row_container(candidate: Any, string_table: List[str], scan_limit: int = 5000) -> Optional[tuple[int, int, int]]:
    """Return (docid_count, dossier_count, total_seen) for a list candidate.

    We scan a limited number of atoms depth-first and resolve string-table indices.
    """
    if not isinstance(candidate, list):
        return None

    docids = 0
    dossiers = 0
    seen = 0

    stack: List[Any] = [candidate]
    while stack and seen < scan_limit:
        x = stack.pop()
        if isinstance(x, list):
            # push children
            for y in reversed(x):
                stack.append(y)
            continue
        seen += 1
        s = _resolve_atom(x, string_table)
        if not s:
            continue
        if RE_DOCID.fullmatch(s):
            docids += 1
        elif RE_DOSSIER_GENERIC.fullmatch(s):
            dossiers += 1

    return docids, dossiers, seen


def _find_best_top_level_window(parsed: List[Any], string_table: List[str], target_rows: int = 20) -> Optional[tuple[int, int]]:
    """Find the best contiguous window within the *top-level* parsed array.

    The Tribuna payloads often contain multiple unrelated segments (string table, caches, older tables).
    A reliable signal for the real UI rows is a *dense* block of ~20 DocIds near each other.

    Strategy:
    - Build a resolved top-level token stream (strings + resolved string-table indices)
    - Slide a window over *DocId occurrences* (not raw indices)
    - Score candidate segments by newest ISO date in the segment, then dossier-like sequences,
      then ISO-date count, then narrower width.

    If we can't find a good segment, return None so callers can fall back to regex extraction.
    """

    stream: List[Optional[str]] = [_resolve_atom(x, string_table) for x in parsed]

    doc_positions: List[int] = [i for i, s in enumerate(stream) if s and RE_DOCID.fullmatch(s)]
    if len(doc_positions) < target_rows:
        return None

    pre_margin = 80
    post_margin = 140
    # Allow a little drift (duplicates/missing) but avoid huge noisy segments.
    min_unique_docids = max(10, target_rows - 5)
    max_unique_docids = target_rows + 25
    max_width = 2500

    best: Optional[tuple[str, int, int, int, int]] = None
    # best = (max_iso_date, dossier_hits, iso_date_count, -width, left)

    def count_dossier_hits(left: int, right: int) -> int:
        hits = 0
        # Match either single-token dossier strings OR 3-token split sequences ("100", "2024", "331").
        for i in range(left, right):
            s = stream[i]
            if not s:
                continue
            if RE_DOSSIER_GENERIC.fullmatch(s):
                hits += 1
                continue
            if i + 2 < right:
                a = stream[i]
                b = stream[i + 1]
                c = stream[i + 2]
                if a and b and c:
                    joined = f"{a} {b} {c}"
                    if RE_DOSSIER_GENERIC.fullmatch(joined):
                        hits += 1
        return hits

    for start in range(0, len(doc_positions) - target_rows + 1):
        doc_l = doc_positions[start]
        doc_r = doc_positions[start + target_rows - 1]
        left = max(0, doc_l - pre_margin)
        right = min(len(stream), doc_r + post_margin)
        width = right - left
        if width <= 0 or width > max_width:
            continue

        uniq_docids: set[str] = set()
        iso_dates: List[str] = []
        for i in range(left, right):
            s = stream[i]
            if not s:
                continue
            if RE_DOCID.fullmatch(s):
                uniq_docids.add(s)
            elif RE_ISO_DATE.fullmatch(s):
                iso_dates.append(s)

        if len(uniq_docids) < min_unique_docids or len(uniq_docids) > max_unique_docids:
            continue

        max_iso = max(iso_dates) if iso_dates else ''
        if not max_iso:
            continue

        dossier_hits = count_dossier_hits(left, right)
        iso_count = len(iso_dates)

        cur = (max_iso, dossier_hits, iso_count, -width, left)
        if best is None or cur > best:
            best = cur

    if best is None:
        return None

    _max_iso, _dh, _ic, _nw, left = best
    # Rebuild right bound: take the densest docid span around that start.
    # Keep it simple by extending until we pass ~target_rows docids.
    docids_seen = 0
    right = left
    while right < len(stream) and docids_seen < target_rows:
        s = stream[right]
        if s and RE_DOCID.fullmatch(s):
            docids_seen += 1
        right += 1
    # Add a small margin to capture dates/paths that follow the last docid.
    right = min(len(stream), right + 120)
    return left, right


def _score_token_stream(tokens: List[str], target_rows: int = 20) -> tuple[int, int, int, int]:
    """Score a token stream for being a good representation of one UI results page.

    Notes:
    - Some extraction strategies (like full string-table inflation) may contain repeated DocIds.
      In that case, *unique* DocIds are a more reliable signal than total occurrences.
    - We still lightly penalize extreme repetition/noise to avoid choosing huge, unfocused streams.
    """
    if not tokens:
        return (-target_rows, 0, 0, 0)

    def is_plausible_iso_date(value: str) -> bool:
        if not value or not RE_ISO_DATE.fullmatch(value):
            return False
        # GWT payloads sometimes contain placeholder dates; don't count them.
        if value == '0000-00-00':
            return False
        try:
            y = int(value[0:4])
            m = int(value[5:7])
            d = int(value[8:10])
        except Exception:
            return False
        if y < 1900 or y > 2100:
            return False
        if m < 1 or m > 12:
            return False
        if d < 1 or d > 31:
            return False
        return True

    uniq_docids: set[str] = set()
    docid_occ = 0
    iso_dates = 0
    for tok in tokens:
        if not tok:
            continue
        if RE_DOCID.fullmatch(tok):
            docid_occ += 1
            uniq_docids.add(tok)
            continue
        if is_plausible_iso_date(tok):
            iso_dates += 1

    uniq = len(uniq_docids)
    deviation = abs(uniq - target_rows)

    # Allow some repetition (multiple references per row), but penalize excessive noise.
    allowed = uniq * 3
    excess = max(0, docid_occ - allowed)
    repetition_penalty = excess // 20

    length_penalty = len(tokens) // 20000

    # Score (lexicographic):
    # 1) prefer ~target_rows unique docids
    # 2) strongly prefer locality (avoid highly repetitive streams)
    # 3) prefer more ISO dates
    # 4) avoid extremely long streams
    return (-deviation, -repetition_penalty, iso_dates, -length_penalty)


def extract_tokens(payload: str) -> List[str]:
    """Return the list of tokens from a raw GWT payload.

    Preferred: parse the //OK[...] array and inflate numeric references into actual strings.
    Fallback: regex-based extraction of quoted strings.
    """
    raw = payload or ''
    parsed_tokens: List[str] = []
    inflated_tokens: List[str] = []
    parsed_score: Optional[tuple[int, int, int, int]] = None
    inflated_score: Optional[tuple[int, int, int, int]] = None
    try:
        parsed = parse_ok_array(raw)
        st = _find_string_table(parsed)
        if st:
            try:
                inflated_tokens = _inflate_tokens(parsed, st)
                inflated_score = _score_token_stream(inflated_tokens)
            except Exception:
                inflated_tokens = []
                inflated_score = None
            win = _find_best_top_level_window(parsed, st, target_rows=20)
            if win is not None:
                l, r = win
                stream = [_resolve_atom(x, st) for x in parsed[l:r]]
                inflated = [s for s in stream if s]
                if inflated:
                    parsed_tokens = inflated
                    parsed_score = _score_token_stream(parsed_tokens)
    except Exception as exc:
        logger.debug("GWT parsing fallback: %s", exc)

    text = strip_gwt_prefix(raw)
    raw_tokens = RE_ALL.findall(text)
    raw_score = _score_token_stream(raw_tokens)

    # Prefer the best of: fully inflated, parsed-window, or raw regex extraction.
    candidates: list[tuple[tuple[int, int, int, int], List[str], str]] = []
    if inflated_tokens and inflated_score is not None:
        candidates.append((inflated_score, inflated_tokens, 'inflated'))
    if parsed_tokens:
        if parsed_score is None:
            parsed_score = _score_token_stream(parsed_tokens)
        candidates.append((parsed_score, parsed_tokens, 'parsed_window'))
    candidates.append((raw_score, raw_tokens, 'raw_regex'))

    # Pick highest score.
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_tokens, best_name = candidates[0]

    # Optional debug when raw regex wins despite having other candidates.
    if best_name == 'raw_regex' and len(candidates) > 1:
        try:
            logger.debug(
                "Token selection picked raw_regex score=%s over %s",
                best_score,
                [(name, score) for score, _toks, name in candidates[1:3]],
            )
        except Exception:
            pass

    return best_tokens


def find_docid_indexes(tokens: List[str], docid_pattern: re.Pattern) -> List[int]:
    """Localise les positions des DocIds dans une liste de tokens.
    
    Args:
        tokens: Liste des tokens extraits du payload GWT
        docid_pattern: Pattern regex compilé pour matcher les DocIds
        
    Returns:
        Liste des indices où des DocIds ont été trouvés
    """
    out: List[int] = []
    for i, value in enumerate(tokens):
        try:
            if docid_pattern.fullmatch(value or ''):
                out.append(i)
        except Exception:
            continue
    return out


def build_pdf_candidates_from_decrypt(
    response_text: str,
    download_url: str,
    base_url: str,
    dossier_fallback: str,
) -> Optional[Tuple[str, List[str]]]:
    """Construit les URLs candidates PDF depuis une réponse decrypt.
    
    La réponse decrypt contient un "partURL" qui permet de construire
    plusieurs URLs candidates. On les teste dans l'ordre jusqu'à
    trouver un PDF valide.
    
    Args:
        response_text: Texte brut de la réponse decrypt
        download_url: URL de base pour les téléchargements
        base_url: URL de base du site
        dossier_fallback: Numéro de dossier à utiliser si non trouvé
        
    Returns:
        Tuple (first_url, remaining_candidates) ou None si échec
    """
    pm = re.search(r'"partURL"\s*,\s*"([^"\\]+)"', response_text or '')
    part = pm.group(1) if pm else None
    if not part:
        return None

    # dossiernummer from response or fallback
    dm = re.search(r'"dossiernummer"\s*,\s*"([^"\\]+)"', response_text or '')
    dossier = dm.group(1) if dm else (dossier_fallback or '')
    try:
        hash_token = part.rsplit('_', 1)[-1]
    except Exception:
        hash_token = part

    qs = urlencode({'path': hash_token, 'pathIsEncrypted': '1', 'dossiernummer': dossier})
    base_dl = (download_url or '').rstrip('/')
    servlet_candidates = [
        f"{base_dl}/tribunavtplus/ServletDownload/{dossier}_{hash_token}?{qs}",
        f"{base_dl}/tribunavtplus/ServletDownload/{dossier}_{hash_token}.pdf?{qs}",
    ]

    base = (base_url or base_dl).rstrip('/')
    candidates: List[str] = list(servlet_candidates)

    try:
        parts = part.split('_')
    except Exception:
        parts = []
    if len(parts) >= 4:
        candidates.extend([
            f"{base}/tribunavtplus/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
            f"{base}/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
            f"{base_dl}/tribunavtplus/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
            f"{base_dl}/pdf/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}.pdf",
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


def is_pdf_ok(response: Response) -> bool:
    """Vérifie si une réponse HTTP contient un PDF valide.
    
    Critères de validation:
    - Status HTTP 200 ou 206 (partial content)
    - Content-Type contient 'pdf' OU URL se termine par .pdf
    
    Args:
        response: Réponse Scrapy à vérifier
        
    Returns:
        True si la réponse semble être un PDF valide
    """
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
