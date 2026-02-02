import json

# Charger les deux fichiers
old_file = 'output/publications/publications_fribourg_20260129_234706.json'
new_file = 'output/publications/publications_fribourg_20260202_122627.json'

old_data = json.load(open(old_file, encoding='utf-8'))
new_data = json.load(open(new_file, encoding='utf-8'))

# Analyser l'ordre des items et leurs PDatum dans les deux runs
print('=== ANCIEN RUN (29/01) - Page 0, premiers 15 items ===')
print('Position | DocId     | Num           | PDatum')
print('-' * 60)
page0_old = [x for x in old_data if x.get('PageNr') == 0][:15]
for i, item in enumerate(page0_old):
    doc = item['DocId'][:8]
    num = item.get('Num', '')
    pd = item.get('PDatum', '')
    print(f'{i:8} | {doc}... | {num:13} | {pd}')

print('\n=== NOUVEAU RUN (02/02) - Page 0, premiers 15 items ===')
print('Position | DocId     | Num           | PDatum')
print('-' * 60)
page0_new = [x for x in new_data if x.get('PageNr') == 0][:15]
for i, item in enumerate(page0_new):
    doc = item['DocId'][:8]
    num = item.get('Num', '')
    pd = item.get('PDatum', '')
    print(f'{i:8} | {doc}... | {num:13} | {pd}')

# Compter les PDatum distincts
print('\n=== Distribution des PDatum ===')
old_pdatums = {}
new_pdatums = {}
for item in old_data:
    pd = item.get('PDatum', 'None')
    old_pdatums[pd] = old_pdatums.get(pd, 0) + 1
for item in new_data:
    pd = item.get('PDatum', 'None')
    new_pdatums[pd] = new_pdatums.get(pd, 0) + 1

print('Ancien run (29/01):')
for pd in sorted(old_pdatums.keys(), reverse=True):
    print(f'  {pd}: {old_pdatums[pd]} items')

print('\nNouveau run (02/02):')
for pd in sorted(new_pdatums.keys(), reverse=True):
    print(f'  {pd}: {new_pdatums[pd]} items')
