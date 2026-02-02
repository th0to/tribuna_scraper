import json
import sys

filename = sys.argv[1] if len(sys.argv) > 1 else 'output/fribourg_test.json'
data = json.load(open(filename, encoding='utf-8'))
print(f'File: {filename}')
print(f'Total: {len(data)} items')
for i in data[:15]:
    doc = i.get('DocId', '')[:8]
    pd = i.get('PDatum', '')
    ed = i.get('EDatum', '')
    print(f'{doc}... PDatum={pd} EDatum={ed}')
