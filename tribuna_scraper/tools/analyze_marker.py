"""Analyse détaillée du mécanisme de marker pour comprendre la propagation."""
import json

old_file = 'output/publications/publications_fribourg_20260129_234706.json'
new_file = 'output/publications/publications_fribourg_20260202_122627.json'

old_data = json.load(open(old_file, encoding='utf-8'))
new_data = json.load(open(new_file, encoding='utf-8'))

print("=" * 80)
print("ANALYSE: Pourquoi seuls les items 2026-01-13 héritent du marker 2026-01-30 ?")
print("=" * 80)

# Regarder la page 0 du nouveau run en détail
print("\n=== NOUVEAU RUN (02/02) - Page 0 complète ===")
print("Pos | DocId     | PDatum     | Observation")
print("-" * 70)

page0_new = [x for x in new_data if x.get('PageNr') == 0]
prev_pdatum = None
for i, item in enumerate(page0_new):
    doc = item['DocId'][:8]
    pd = item.get('PDatum', '')
    
    # Détecter les changements de PDatum
    if prev_pdatum and pd != prev_pdatum:
        obs = f"<-- CHANGEMENT de {prev_pdatum} vers {pd}"
    elif i == 0:
        obs = "<-- PREMIER ITEM (définit le marker initial)"
    else:
        obs = ""
    
    print(f"{i:3} | {doc}... | {pd} | {obs}")
    prev_pdatum = pd

# Analyser la structure
print("\n" + "=" * 80)
print("EXPLICATION DU MÉCANISME")
print("=" * 80)

# Grouper par PDatum
pdatum_groups = {}
for i, item in enumerate(page0_new):
    pd = item.get('PDatum', '')
    if pd not in pdatum_groups:
        pdatum_groups[pd] = []
    pdatum_groups[pd].append((i, item['DocId'][:8], item.get('Num', '')))

print("\nGroupes par PDatum sur la page 0:")
for pd in sorted(pdatum_groups.keys(), reverse=True):
    items = pdatum_groups[pd]
    positions = [str(p[0]) for p in items]
    print(f"\n  {pd}: {len(items)} items aux positions {', '.join(positions)}")
    
print("""

HYPOTHÈSE:
---------
Le marker se propage séquentiellement MAIS se "réinitialise" quand on rencontre
un chunk avec 2+ dates distinctes où le MAX est différent du marker actuel.

Dans le contenu GWT de la page 0 du 02/02:
1. DocId 414447d6 crée le marker 2026-01-30 (premier item, date la plus récente)
2. Les DocIds suivants (anciens items du 2026-01-13) héritent de ce marker
3. À un certain point, on rencontre des DocIds dont le chunk contient 
   des dates comme 2026-01-12 sans trace de 2026-01-30
   → Le marker n'est PAS appliqué car la règle est "upgrade only"
   
La clé: le marker ne peut que UPGRADER une date, jamais la downgrader.
Si un DocId a déjà trouvé 2026-01-12 localement, et que le marker est 2026-01-30,
le marker s'applique. MAIS si le marker descend (nouvelle section), 
les items de cette section gardent leurs dates locales.
""")

# Vérifier l'ordre dans l'ancien run
print("\n=== COMPARAISON: Position des mêmes DocIds entre les deux runs ===")
print("\nDocIds du 2026-01-13 (ancien) vs leur position dans le nouveau run:")

old_0113_docids = [i['DocId'] for i in old_data if i.get('PDatum') == '2026-01-13']
new_docid_to_pos = {item['DocId']: i for i, item in enumerate(page0_new)}

for docid in old_0113_docids:
    old_pos = next((i for i, x in enumerate(old_data) if x['DocId'] == docid), -1)
    new_pos = new_docid_to_pos.get(docid, -1)
    new_pd = next((x.get('PDatum') for x in new_data if x['DocId'] == docid), '-')
    print(f"  {docid[:8]}... : ancien pos={old_pos}, nouveau pos={new_pos}, nouveau PDatum={new_pd}")

print("\nDocIds du 2026-01-12 (ancien) vs leur position dans le nouveau run:")
old_0112_docids = [i['DocId'] for i in old_data if i.get('PDatum') == '2026-01-12']

for docid in old_0112_docids[:5]:  # Premiers 5 seulement
    old_pos = next((i for i, x in enumerate(old_data) if x['DocId'] == docid), -1)
    new_pos = new_docid_to_pos.get(docid, -1)
    new_pd = next((x.get('PDatum') for x in new_data if x['DocId'] == docid), '-')
    print(f"  {docid[:8]}... : ancien pos={old_pos}, nouveau pos={new_pos}, nouveau PDatum={new_pd}")
