"""
Analyse de la position des DocIds dans le contenu pour comprendre 
pourquoi certains héritent du marker et d'autres non.
"""
import json

new_file = 'output/publications/publications_fribourg_20260202_122627.json'
new_data = json.load(open(new_file, encoding='utf-8'))

# Les items de la page 0
page0 = [x for x in new_data if x.get('PageNr') == 0]

print("Analyse de la page 0 du run 02/02")
print("=" * 80)
print("\nL'ordre dans le JSON reflète l'ordre de TRAITEMENT (émission), pas")
print("nécessairement l'ordre dans le contenu GWT.\n")

# Grouper par PDatum
by_pdatum = {}
for item in page0:
    pd = item.get('PDatum', 'None')
    if pd not in by_pdatum:
        by_pdatum[pd] = []
    by_pdatum[pd].append(item)

print("Distribution des PDatum:")
for pd in sorted(by_pdatum.keys(), reverse=True):
    print(f"  {pd}: {len(by_pdatum[pd])} items")

print("\n" + "=" * 80)
print("HYPOTHÈSE CORRIGÉE")
print("=" * 80)
print("""
La vraie question est: pourquoi 60c46ac0 et a1807eca (2026-01-12) ne sont 
pas affectés par le marker 2026-01-30 ?

Regardons leur position dans l'output:
- Position 0: 414447d6 → PDatum=2026-01-30 (définit le marker)
- Position 1: 60c46ac0 → PDatum=2026-01-12 (!!)
- Position 2: a1807eca → PDatum=2026-01-12 (!!)
- Position 3+: items avec PDatum=2026-01-30

EXPLICATION PROBABLE:
--------------------
Dans le contenu GWT, les DocIds 60c46ac0 et a1807eca apparaissent AVANT
le DocId 414447d6 dans le texte brut. 

Le mécanisme de marker calcule le hint basé sur la POSITION dans le contenu,
pas sur l'ordre de traitement des tokens.

Donc:
1. Le marker est calculé en scannant le contenu de gauche à droite
2. 60c46ac0 et a1807eca sont rencontrés EN PREMIER (avant 414447d6)
3. À ce moment, pas encore de marker → ils gardent leur date locale 2026-01-12
4. Ensuite 414447d6 crée le marker 2026-01-30
5. Les DocIds suivants héritent de ce marker

C'est pourquoi les items 2026-01-12 apparaissent aux positions 1 et 2 dans
l'output final (ils sont traités tôt car leurs tokens sont extraits tôt)
mais ils ont été scannés AVANT la création du marker.
""")

print("\nVérification - ordre des DocIds dans le JSON (ordre d'émission):")
for i, item in enumerate(page0[:8]):
    print(f"  {i}: {item['DocId'][:8]}... PDatum={item.get('PDatum')}")
