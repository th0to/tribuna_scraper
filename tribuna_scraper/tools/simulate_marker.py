"""
Simulation du mécanisme de marker pour comprendre la propagation.

Ce script reproduit la logique de _compute_pdatum_hint_by_docid_for_page()
avec des logs détaillés.
"""
import re
import json
from datetime import date, datetime, timedelta, timezone

RE_DATE_ISO = re.compile(r'\b(20[012]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b')
RE_DATE_CH = re.compile(r'\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(20[012]\d)\b')
RE_ID = re.compile(r'[0-9a-f]{32}')

def parse_date(s):
    if not s:
        return None
    # ISO
    m = RE_DATE_ISO.match(str(s).strip())
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except:
            pass
    # CH format
    m = RE_DATE_CH.match(str(s).strip())
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except:
            pass
    return None

def scan_dates(text):
    results = []
    for m in RE_DATE_ISO.finditer(text):
        d = parse_date(m.group())
        if d:
            results.append(d.isoformat())
    for m in RE_DATE_CH.finditer(text):
        d = parse_date(m.group())
        if d:
            results.append(d.isoformat())
    return results

# Lire un fichier GWT récent
import glob
gwt_files = sorted(glob.glob('output/gwt_responses/gwt_response_fribourg_*_p0.txt'), reverse=True)

if not gwt_files:
    print("Pas de fichier GWT trouvé, lançons un crawl rapide...")
    import subprocess
    subprocess.run(['scrapy', 'crawl', 'fribourg', '-a', 'days=1', '-s', 'MAX_PAGES=1', 
                   '-s', 'LOG_LEVEL=WARNING', '-s', 'FILES_STORE=output/pdfs/test'])
    gwt_files = sorted(glob.glob('output/gwt_responses/gwt_response_fribourg_*_p0.txt'), reverse=True)

if gwt_files:
    print(f"Analyse du fichier: {gwt_files[0]}\n")
    content = open(gwt_files[0], encoding='utf-8', errors='replace').read()
    
    # Trouver tous les DocIds et leurs positions
    docid_positions = {}
    for m in RE_ID.finditer(content):
        docid = m.group()
        if docid not in docid_positions:
            docid_positions[docid] = []
        docid_positions[docid].append(m.start())
    
    # Garder seulement les DocIds avec une seule occurrence (moins ambigus)
    anchors = []
    for docid, positions in docid_positions.items():
        if len(positions) == 1:
            anchors.append((positions[0], docid))
    
    anchors.sort(key=lambda x: x[0])
    print(f"Nombre de DocIds uniques trouvés: {len(anchors)}\n")
    
    # Simuler la propagation du marker
    print("=" * 100)
    print("SIMULATION DE LA PROPAGATION DU MARKER")
    print("=" * 100)
    print(f"{'Pos':>6} | {'DocId':10} | {'Dates dans chunk':40} | {'Marker':12} | Note")
    print("-" * 100)
    
    current_marker = None
    today = datetime.now(timezone.utc).date()
    future_cutoff = today + timedelta(days=400)
    
    for i, (pos0, doc_id) in enumerate(anchors[:25]):  # Premiers 25
        # Fin du chunk = début du prochain DocId
        pos1 = anchors[i + 1][0] if i + 1 < len(anchors) else len(content)
        chunk = content[pos0:min(pos1, pos0 + 5000)]  # Limiter pour lisibilité
        
        iso_list = scan_dates(chunk)
        uniq_iso = list(dict.fromkeys(iso_list))  # Dédupliquer en gardant l'ordre
        
        # Filtrer dates futures
        valid_dates = []
        for iso in uniq_iso:
            d = parse_date(iso)
            if d and d <= future_cutoff:
                valid_dates.append(d)
        
        distinct_set = set(d.isoformat() for d in valid_dates)
        dates_str = ', '.join(sorted(distinct_set, reverse=True)[:4])
        if len(distinct_set) > 4:
            dates_str += f" (+{len(distinct_set)-4} autres)"
        
        note = ""
        
        # Si 2+ dates distinctes, potentiel nouveau marker
        if len(distinct_set) >= 2:
            marker_candidate = max(valid_dates)
            
            if current_marker is None:
                current_marker = marker_candidate
                note = f"NOUVEAU MARKER: {marker_candidate}"
            elif marker_candidate > current_marker:
                note = f"Marker candidat {marker_candidate} > actuel, IGNORÉ (desc)"
            elif marker_candidate < current_marker:
                current_marker = marker_candidate
                note = f"MARKER DESCEND: {marker_candidate}"
            else:
                note = f"Marker inchangé"
        else:
            if valid_dates:
                note = f"1 seule date, marker hérité"
            else:
                note = "Pas de date"
        
        marker_str = current_marker.isoformat() if current_marker else "-"
        print(f"{i:>6} | {doc_id[:8]}.. | {dates_str:40} | {marker_str:12} | {note}")
    
    print("\n" + "=" * 100)
    print("EXPLICATION")
    print("=" * 100)
    print("""
Le marker se propage ainsi:
1. Quand un chunk contient 2+ dates DISTINCTES, on prend le MAX comme marker
2. En mode DESC (plus récent d'abord), le marker ne peut que DESCENDRE
3. Si marker_candidat > marker_actuel, on l'ignore (on garde l'ancien)
4. Si un chunk n'a qu'1 date ou 0 date, le DocId hérite du marker actuel

DONC: Les items du 2026-01-13 étaient les premiers dans l'ancien run.
Dans le nouveau run, un nouvel item (2026-01-30) apparaît AVANT eux.
Ce nouvel item crée un marker 2026-01-30 qui se propage à tous les suivants.

Les items 2026-01-12 gardent leur date car:
- Soit ils sont dans une section où le marker a été réinitialisé
- Soit leur chunk contient explicitement 2026-01-12 qui "gagne" via _search_pdatum_in_content
""")
else:
    print("Aucun fichier GWT trouvé")
