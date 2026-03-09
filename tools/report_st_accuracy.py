"""Rapport de précision PDatum pour une sortie spider vs PDatumListe ground truth.

Usage:
    python tools/report_st_accuracy.py [output/<run>.json]

Par défaut, utilise le JSON Fribourg le plus récent dans output/publications/.
"""
import json
import sys
from pathlib import Path
from collections import Counter

BASE = Path(__file__).parent.parent

def _find_latest_json() -> Path:
    candidates = sorted(
        (BASE / 'output' / 'publications').glob('publications_fribourg_*.json'),
        reverse=True
    )
    if not candidates:
        raise FileNotFoundError("Aucun fichier publications_fribourg_*.json dans output/publications/")
    return candidates[0]

json_path = Path(sys.argv[1]) if len(sys.argv) >= 2 else _find_latest_json()
pdlist_path = BASE / 'readme' / 'PDatumListe.updated.txt'

print(f"JSON spider  : {json_path}")
print(f"Ground truth : {pdlist_path}")
print()

# Charger les items scrapés
with open(json_path, encoding='utf-8') as f:
    items = json.load(f)

# Parser le ground truth PDatumListe
gt = {}  # docid -> pdatum ISO
with open(pdlist_path, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line or ';' not in line:
            continue
        parts = [p.strip() for p in line.split(';')]
        if len(parts) >= 4 and parts[3] and len(parts[3].strip()) == 32:
            docid = parts[3].strip()
            pd_str = parts[0].strip()
            try:
                d, m, y = pd_str.split('.')
                gt[docid] = f'{y}-{m}-{d}'
            except Exception:
                pass

total = len(items)
st_count = sum(1 for i in items if i.get('pdatum_source', '') == 'st')
legacy_count = sum(1 for i in items if i.get('pdatum_source', '') not in ('st', '', None))
no_source = sum(1 for i in items if not i.get('pdatum_source', ''))

# Vagues PDatum dans le JSON
pdatum_dist = Counter(i.get('publikationsDatum', 'N/A') for i in items)

matched = 0
wrong = []
no_gt = 0

for item in items:
    docid = item.get('id_doc', '')
    pdatum = item.get('publikationsDatum', '')
    src = item.get('pdatum_source', '')
    if docid not in gt:
        no_gt += 1
        continue
    if pdatum == gt[docid]:
        matched += 1
    else:
        wrong.append({
            'num': item.get('num', '?'),
            'docid': docid,
            'got': pdatum,
            'expected': gt[docid],
            'src': src,
        })

comparable = total - no_gt
acc = matched / comparable * 100 if comparable else 0

sep = '=' * 55
print(sep)
print('  COMPTE RENDU — SPIDER fribourg -a days=30')
print('  Algorithme ST-based PDatum (intégration)')
print(sep)
print()
print('── ITEMS SCRAPÉS ─────────────────────────────────')
print(f'  Total items           : {total}')
print(f'  Source PDatum = ST    : {st_count} ({st_count/total*100:.1f}%)')
print(f'  Source PDatum = legacy: {legacy_count} ({legacy_count/total*100:.1f}%)')
print(f'  Source PDatum absente : {no_source}')
print()
print('── DISTRIBUTION VAGUES PDatum ────────────────────')
for pd, cnt in sorted(pdatum_dist.items(), reverse=True)[:10]:
    bar = '█' * cnt
    print(f'  {pd} : {cnt:>3}  {bar}')
print()
print('── VALIDATION vs PDATUMLISTE (GROUND TRUTH) ──────')
print(f'  Items comparables     : {comparable}/{total}')
print(f'  Hors GT (nouveaux)    : {no_gt}')
print(f'  Corrects              : {matched}/{comparable}')
print(f'  Précision             : {acc:.1f}%')
print()

if wrong:
    print(f'── ERREURS ({len(wrong)}) ─────────────────────────────────')
    for w in wrong:
        print(f'  Num={w["num"]}')
        print(f'    DocId    : {w["docid"]}')
        print(f'    Obtenu   : {w["got"]}')
        print(f'    Attendu  : {w["expected"]}')
        print(f'    Source   : {w["src"]}')
else:
    print('── ERREURS ───────────────────────────────────────')
    print('  ✅ Aucune erreur PDatum')

print()
print(sep)
# Résumé verdict
prev_acc = 90.9
delta = acc - prev_acc
arrow = '↑' if delta >= 0 else '↓'
print(f'  Précision précédente (legacy) : {prev_acc:.1f}%')
print(f'  Précision actuelle  (ST-based): {acc:.1f}%')
print(f'  Amélioration        : {arrow} {abs(delta):.1f} pts')
print(sep)
