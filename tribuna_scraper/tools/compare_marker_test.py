"""Compare les résultats avec et sans marker."""
import json

# Fichier avec marker (run précédent)
with_marker = json.load(open('output/publications/publications_fribourg_20260202_122627.json', encoding='utf-8'))

# Fichier sans marker (test)
no_marker = json.load(open('output/no_marker_test.json', encoding='utf-8'))

print("=" * 90)
print("COMPARAISON: AVEC MARKER vs SANS MARKER")
print("=" * 90)

print(f"\nNombre d'items avec marker: {len(with_marker)}")
print(f"Nombre d'items sans marker: {len(no_marker)}")

# Créer des dicts par DocId
with_dict = {i['DocId']: i for i in with_marker}
no_dict = {i['DocId']: i for i in no_marker}

# Comparer les PDatum
print("\n" + "-" * 90)
print("Items dont le PDatum DIFFÈRE entre les deux modes:")
print("-" * 90)
print(f"{'DocId':10} | {'Num':15} | {'Avec marker':12} | {'Sans marker':12} | Diff")
print("-" * 90)

diff_count = 0
same_count = 0

for docid in with_dict:
    if docid in no_dict:
        pd_with = with_dict[docid].get('PDatum', '')
        pd_no = no_dict[docid].get('PDatum', '')
        
        if pd_with != pd_no:
            diff_count += 1
            num = with_dict[docid].get('Num', '')[:15]
            print(f"{docid[:8]}.. | {num:15} | {pd_with:12} | {pd_no:12} | DIFFÉRENT")
        else:
            same_count += 1

print(f"\nRésumé: {diff_count} items différents, {same_count} items identiques")

# Distribution des PDatum
print("\n" + "=" * 90)
print("DISTRIBUTION DES PDATUM")
print("=" * 90)

def count_pdatums(data):
    counts = {}
    for item in data:
        pd = item.get('PDatum', 'None')
        counts[pd] = counts.get(pd, 0) + 1
    return counts

pd_with = count_pdatums(with_marker)
pd_no = count_pdatums(no_marker)

all_dates = sorted(set(pd_with.keys()) | set(pd_no.keys()), reverse=True)

print(f"\n{'PDatum':12} | {'Avec marker':12} | {'Sans marker':12}")
print("-" * 45)
for pd in all_dates:
    w = pd_with.get(pd, 0)
    n = pd_no.get(pd, 0)
    marker = " <-- DIFF" if w != n else ""
    print(f"{pd:12} | {w:12} | {n:12}{marker}")

# Vérifier si les items du 2026-01-13 apparaissent maintenant
print("\n" + "=" * 90)
print("VÉRIFICATION: Items qui avaient PDatum=2026-01-13 dans l'ancien run")
print("=" * 90)

old_run = json.load(open('output/publications/publications_fribourg_20260129_234706.json', encoding='utf-8'))
old_0113 = [i for i in old_run if i.get('PDatum') == '2026-01-13']

print(f"\nDocIds avec PDatum=2026-01-13 dans le run du 29/01:")
for item in old_0113:
    docid = item['DocId']
    if docid in no_dict:
        new_pd = no_dict[docid].get('PDatum', '-')
        print(f"  {docid[:8]}... Sans marker: PDatum={new_pd}")
    else:
        print(f"  {docid[:8]}... NON TROUVÉ dans le test sans marker")

# Items perdus ?
print("\n" + "=" * 90)
print("ANALYSE DES ITEMS PERDUS/GAGNÉS")
print("=" * 90)

only_with = set(with_dict.keys()) - set(no_dict.keys())
only_no = set(no_dict.keys()) - set(with_dict.keys())

print(f"\nItems présents AVEC marker mais ABSENTS sans marker: {len(only_with)}")
for docid in list(only_with)[:5]:
    item = with_dict[docid]
    print(f"  {docid[:8]}... PDatum={item.get('PDatum')} Num={item.get('Num', '')[:20]}")

print(f"\nItems présents SANS marker mais ABSENTS avec marker: {len(only_no)}")
for docid in list(only_no)[:5]:
    item = no_dict[docid]
    print(f"  {docid[:8]}... PDatum={item.get('PDatum')} Num={item.get('Num', '')[:20]}")
