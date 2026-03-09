# Algorithme ST-Based pour l'extraction de PDatum — Documentation détaillée

> **Date** : 05.03.2026
> **Scope** : Fribourg uniquement (canton FR, publicationtc.fr.ch)
> **Performance** : 98.1% PDatum (157/160) sur 8 pages fraîches, soit 99.4% si on exclut les items absents du ground truth

---

## Table des matières

1. [Contexte et motivation](#1-contexte-et-motivation)
2. [Anatomie d'une réponse GWT](#2-anatomie-dune-réponse-gwt)
3. [Structure de la String Table](#3-structure-de-la-string-table)
4. [Règles découvertes](#4-règles-découvertes)
5. [Algorithme pas à pas](#5-algorithme-pas-à-pas)
6. [Exemples concrets](#6-exemples-concrets)
7. [Limitation structurelle](#7-limitation-structurelle)
8. [Comparaison avec l'approche actuelle](#8-comparaison-avec-lapproche-actuelle)
9. [Plan d'intégration](#9-plan-dintégration)
10. [Outils d'analyse](#10-outils-danalyse)

---

## 1. Contexte et motivation

### Le problème

Le spider Fribourg extrait 2 dates par décision judiciaire :
- **EDatum** (Entscheidungsdatum) : date de la décision
- **PDatum** (Publikationsdatum) : date de publication sur le site ← **champ clé pour le filtrage**

Le PDatum n'est pas encodé explicitement pour chaque item dans la réponse GWT. L'approche précédente (marker propagation via scan du contenu brut) atteint ~83-90% de précision. L'algorithme ST-based exploite l'**ordre de la string table** pour reconstruire les PDatum avec ~98-99% de précision.

### Pourquoi la string table ?

Les réponses GWT-RPC contiennent une **string table** (tableau de chaînes uniques) référencée par des indices numériques (les "atomes"). L'insight clé est :

> **L'ordre des entrées dans la string table correspond à l'ordre des items dans l'UI.**

Les items apparaissent séquentiellement (Item 1, 2, ..., 20) et les dates de publication apparaissent entre les items aux transitions de "vague".

---

## 2. Anatomie d'une réponse GWT

```
//OK[14077,[["tribun...PagingResultSet/..."],0,<atoms>,...,["string1","string2",...,"stringN"]]]
        │                                        │                    │
   trefferzahl                              atomes (int/float)     string table
   (total results)                          (références)          (chaînes uniques)
```

### String Table (ST)

Le **dernier tableau de chaînes** dans la réponse. Contient toutes les valeurs textuelles uniques de la page :
- DocIds (hex 32 chars)
- Numéros de dossier (ex: `101 2025 430`)
- Dates ISO (ex: `2026-03-04`)
- Codes de tribunal (ex: `601`, `102`)
- Descriptions, labels, textes d'aperçu

**Propriété fondamentale** : la ST est **dédupliquée**. Chaque valeur unique apparaît exactement UNE fois, même si elle est référencée par plusieurs atomes/items.

---

## 3. Structure de la String Table

Pour chaque page de 20 items, la ST contient typiquement 147-177 entrées. Voici la structure type :

### Header (avant le 1er item)

```
ST[0-2]  Metadata GWT (nom de classe, etc.)
ST[3]    GCODE du 1er tribunal (ex: "601")
ST[4-5]  Descriptions du tribunal
ST[6]    DOCID du 1er item
ST[7]    Description courte de l'item
ST[8]    NUM (numéro de dossier)
ST[9]    DATE (EDatum du 1er item)
ST[10]   SEP ("-") ← séparateur marquant la fin du header
```

### Zone inter-header (entre SEP et 2ème item)

```
ST[11-17] Autres metadata, textes...
ST[18]    DATE → c'est le PDatum de la 1ère vague
```

### Items suivants (répétition)

```
[GCODE]  ← optionnel, seulement si le tribunal change
DOCID
[descriptions]
NUM
[DATE]   ← EDatum, seulement si c'est une nouvelle valeur (déduplication)
[DATE]   ← optionnel : date d'appel (future), ou PDatum d'une nouvelle vague
```

### Schéma visuel d'une page type

```
┌─────── Header ────────┐
│  GCODE  DOCID₁  NUM₁  │
│  EDatum₁  SEP("-")     │
│  ...metadata...        │
│  PDatum_vague_1        │  ← PDatum de la 1ère vague, entre SEP et Item 2
└────────────────────────┘

┌─────── Vague 1 (items 2..N) ──┐
│  GCODE? DOCID₂ NUM₂ EDatum₂?  │  ← EDatum seulement si nouveau
│  GCODE? DOCID₃ NUM₃           │  ← pas d'EDatum (dédupliqué)
│  GCODE? DOCID₄ NUM₄ EDatum₄?  │
│  ...                           │
│  DOCIDₙ NUMₙ                  │
│          PDatum_vague_2        │  ← PDatum de la vague 2, APRÈS le dernier item de vague 1
└────────────────────────────────┘

┌─────── Vague 2 (items N+1..M) ─┐
│  GCODE? DOCIDₙ₊₁ NUMₙ₊₁       │  ← 1er item de la nouvelle vague
│  DOCIDₙ₊₂ NUMₙ₊₂               │
│  ...                            │
│          PDatum_vague_3         │  ← si il y a une 3ème vague
└─────────────────────────────────┘
```

---

## 4. Règles découvertes

### Règle 1 — EDatum au +1 (`NUM_idx + 1`) [CONFIRMÉ]

> **Si `ST[NUM_idx + 1]` est une date ISO, alors c'est le EDatum de cet item.**
> **Sinon, le EDatum de l'item est une valeur déjà apparue plus haut dans la ST (déduplication).**

Exemples concrets (page 0 fraîche, 05.03.2026) :

| Item | NUM_idx | ST[NUM_idx+1] | EDatum |
|------|---------|---------------|--------|
| #1 | 8 | `2026-02-10` (DATE) | `2026-02-10` ✓ |
| #2 | 23 | `2026-02-16` (DATE) | `2026-02-16` ✓ |
| #3 | 34 | `2026-02-17` (DATE) | `2026-02-17` ✓ |
| #4 | 41 | `"605"` (GCODE) | *dédupliqué* → même EDatum `2026-02-17` que Item #3 |
| #5 | 47 | `"..."` (description) | *dédupliqué* → EDatum `2026-02-16` déjà vu |

**Taux de succès** : ~55-65% des items ont leur EDatum à la position +1. Les autres l'ont dédupliqué (la valeur existe déjà plus haut dans la ST).

### Règle 2 — PDatum strictement décroissant [CONFIRMÉ]

> **Les pages sont triées par PDatum décroissant (du plus récent au plus ancien).**
> **Un nouveau PDatum de vague doit être strictement inférieur au PDatum courant.**

Cela permet de rejeter les "faux positifs" : dates d'appel, dates juridiques, etc. qui sont souvent plus récentes que le PDatum courant.

Exemple sur la page 0 :
```
PDatum vague 1 : 2026-03-04  (items 1-11)
PDatum vague 2 : 2026-03-02  (items 12-20)  ← 2026-03-02 < 2026-03-04 ✓
```

### Règle 3 — PDatum = dernière date non-EDatum non-future dans la zone inter-items [CONFIRMÉ]

> **Le PDatum d'une nouvelle vague est la DERNIÈRE date dans la zone entre deux items consécutifs qui :**
> 1. N'est PAS l'EDatum de l'item courant
> 2. N'est PAS une date future (> scrape_date + 5j)
> 3. N'est PAS `0000-00-00`
> 4. Est strictement < au PDatum courant

On prend la **dernière** car elle est la plus proche du prochain item (et donc la plus susceptible d'être le marqueur de la nouvelle vague).

### Règle 4 — Le PDatum se propage par héritage [CONFIRMÉ]

> **Si la zone après un item ne contient aucune date candidate, l'item hérite du PDatum courant.**

La grande majorité des items (>80%) n'ont aucune date entre leur NUM et le DOCID suivant. Ils héritent tous du PDatum de leur vague.

### Règle 5 — item_start_idx inclut le GCODE [CONFIRMÉ]

> **Un item commence soit à son DOCID, soit au GCODE le précédant (si dans les 5 positions avant).**

Le GCODE (code du tribunal, ex: `"601"`, `"102"`) précède le DOCID quand le tribunal change. Il fait partie de la "zone" de l'item suivant, pas de l'item précédent.

---

## 5. Algorithme pas à pas

### Entrée
- `st` : string table (liste de chaînes)
- `scrape_date` : date du scrape (pour filtrer les dates futures)

### Étape 1 — Classification des entrées ST

Pour chaque entrée `st[i]`, déterminer son type :

| Pattern | Type | Regex |
|---------|------|-------|
| 32 chars hex | `DOCID` | `^[0-9a-f]{32}$` |
| `NNN YYYY NNN` | `NUM` | `^\d{1,4}\s+\d{4}\s+\d+$` |
| `YYYY-MM-DD` | `DATE` | `^(19\|20)\d{2}-(0[1-9]\|1[0-2])-(0[1-9]\|[12]\d\|3[01])$` |
| `0000-00-00` | `ZDATE` | `^0{4}-0{2}-0{2}$` |
| `"-"` | `SEP` | exact match |
| 3 digits | `GCODE` | `^\d{3}$` |

### Étape 2 — Appariement DOCID → NUM

Parcourir les DOCIDs dans l'ordre de la ST. Pour chaque DOCID, trouver le **premier NUM suivant** non encore utilisé. Ceci forme un "item" :

```python
items = []
used_nums = set()
for didx, docid in docid_entries:
    for nidx, num in num_entries:
        if nidx > didx and nidx not in used_nums:
            items.append({docid, num, docid_idx, num_idx})
            used_nums.add(nidx)
            break
```

Résultat : liste ordonnée de 20 items (pour une page standard).

### Étape 3 — Attribution des EDatum (règle +1)

```python
for item in items:
    candidate = st[item.num_idx + 1]
    if is_date(candidate):
        item.edatum = candidate
    # sinon : EDatum dédupliqué (pas d'action, reste None)
```

### Étape 4 — Détection des PDatum (scan des zones inter-items)

```python
current_pdatum = None

for i, item in enumerate(items):
    # Zone = entre NUM de l'item i et le début de l'item i+1
    zone_start = item.num_idx
    zone_end = item_start_idx(items[i+1]) if i+1 < len(items) else len(st)
    
    # Collecter les dates dans la zone
    zone_dates = [d for d in date_entries if zone_start < d.idx < zone_end]
    
    # Filtrer : exclure EDatum, dates futures, zero dates
    candidates = []
    for d in zone_dates:
        if d.val == item.edatum: continue           # Exclure EDatum
        if parse_date(d.val) > scrape_date + 5j: continue  # Exclure futures
        candidates.append(d)
    
    if candidates:
        new_pd = candidates[-1].val  # Dernière date = la plus proche du prochain item
        new_pd_date = parse_date(new_pd)
        current_pd_date = parse_date(current_pdatum)
        
        if current_pd_date is None or new_pd_date < current_pd_date:
            # Nouvelle vague ! (strictement décroissant)
            current_pdatum = new_pd
            item.pdatum = current_pdatum
            item.pdatum_source = 'wave_change'
        else:
            # Date ≥ courant → pas un changement de vague (date d'appel ou metadata)
            item.pdatum = current_pdatum
            item.pdatum_source = 'wave_inherit'
    else:
        # Pas de date dans la zone → héritage
        item.pdatum = current_pdatum
        item.pdatum_source = 'wave_inherit'
```

### Étape 5 — Résultat

Chaque item a maintenant :
- `docid` : identifiant du document
- `num` : numéro de dossier
- `edatum` : date de décision (ou None si dédupliqué)
- `pdatum` : date de publication
- `pdatum_source` : `wave_change` | `wave_inherit` | `explicit_in_zone`

---

## 6. Exemples concrets

### Page 0 (scrape 05.03.2026) — 2 vagues parfaitement détectées

**Vague 1** : PDatum = `2026-03-04` (11 items)

```
ST[6]  DOCID (Item 1)     ST[8]  NUM    ST[9]  EDatum 2026-02-10
ST[10] SEP("-")
ST[18] DATE 2026-03-04 ← PDatum de vague 1 (entre SEP et Item 2)
ST[21] DOCID (Item 2)     ST[23] NUM    ST[24] EDatum 2026-02-16
            ...items 3-11...
```

**Transition vers Vague 2** :

```
ST[85] DOCID (Item 11)    ST[84] NUM    ST[85] EDatum 2026-02-11
       ← zone entre Item 11 et Item 12 →
ST[100] DATE 2026-03-02 ← PDatum vague 2 (2026-03-02 < 2026-03-04 ✓)
ST[101] DOCID (Item 12)
```

**Vague 2** : PDatum = `2026-03-02` (9 items, items 12-20)

Résultat : **20/20 PDatum correct = 100%**

### Page 5 — Cas de limitation (1 erreur)

```
ST[9]  DATE 2026-02-10 ← EDatum de Item 1, MAIS AUSSI le PDatum d'une vague de 1 item (Item 6)
```

Item 6 (`601 2026 17`) devrait avoir PDatum = `2026-02-10`, mais cette date n'existe pas à proximité de l'Item 6 dans la ST — elle est "consommée" par l'Item 1 comme EDatum. La déduplication de la ST rend cette vague invisible.

---

## 7. Limitation structurelle

### Déduplication de la String Table

La ST est **dédupliquée** : chaque valeur unique n'apparaît qu'une seule fois, même si elle est utilisée par plusieurs items/rôles (ex: une date qui est à la fois EDatum d'un item et PDatum d'une vague).

**Conséquence** : quand un PDatum de vague a la **même valeur** qu'un EDatum déjà présent dans la ST, le PDatum n'a pas sa propre entrée. L'algorithme ne peut pas le détecter.

### Fréquence

Sur 160 items analysés (8 pages, scrape 05.03.2026) :
- **1 seule erreur** due à cette limitation (Item 6, page 5)
- C'est typiquement une "micro-vague" (1 seul item) dont le PDatum coïncide avec un EDatum

### Pourquoi c'est irréductible

Aucun algorithme basé **uniquement** sur la string table ne peut résoudre ce cas. Pour le résoudre, il faudrait :
- Analyser les atomes (indices numériques) pour voir combien de fois la date est référencée et distinguer les rôles EDatum/PDatum
- Ou utiliser une source externe (UI, autre endpoint)

### Stratégie de mitigation

L'approche actuelle (marker propagation) peut servir de **fallback** pour ces cas rares. L'intégration prévue combine ST-based (primaire) + marker (fallback).

---

## 8. Comparaison avec l'approche actuelle

### Approche actuelle : marker propagation (`_compute_pdatum_hint_by_docid_for_page`)

| Aspect | Marker propagation | ST-based |
|--------|--------------------|----------|
| **Source** | Scan du contenu brut (positions dans le texte) | Ordre de la string table (structure sémantique) |
| **PDatum** | `max(dates_distinctes_du_chunk)` → propagé | Dernière date non-EDatum entre items → strictement décroissant |
| **EDatum** | Token offset (+2/+3 après DocId dans les tokens) | Règle +1 (`ST[NUM_idx + 1]`) |
| **Précision PDatum** | ~83-90% (20 erreurs sur 120 items historiques) | **98.1%** (3/160, dont 2 items absents du GT) |
| **Erreurs typiques** | "Date sliding" : items en fin de page héritent du mauvais marker | Micro-vagues invisibles (déduplication ST) |
| **Complexité** | ~200 lignes de code, 6 étapes avec scoring | ~100 lignes, 4 étapes linéaires |
| **Robustesse** | Sensible aux positions dans le contenu brut | Dépend de l'ordre ST (stable, confirmé sur 8+ pages) |

### Pourquoi ST-based est meilleur

1. **Exploite la structure sémantique** : l'ordre ST reflète l'ordre UI (confirmé)
2. **Pas de scoring heuristique** : règles binaires (est-ce une date ? est-elle < courant ?)
3. **Pas de fenêtre de recherche arbitraire** : les zones sont bornées par les items adjacents
4. **Pas de confusion EDatum/PDatum** : la règle +1 identifie le EDatum sans ambiguïté

---

## 9. Plan d'intégration

### Stratégie recommandée

**Remplacer** la logique actuelle de détection PDatum par l'algorithme ST-based, en gardant le marker comme fallback léger.

### Modifications dans `fribourg_spider.py`

1. **Nouvelle méthode** : `_extract_pdatum_from_string_table(st, scrape_date)` dans `fribourg_spider.py`
   - Prend la string table en entrée (déjà parsée via `gwt_utils.extract_tokens`)
   - Retourne un `dict[str, dict]` mappant chaque DocId à `{pdatum, edatum, pdatum_source}`

2. **Modification de `parse_page`** :
   - Appeler `_extract_pdatum_from_string_table` une fois par page
   - Utiliser le résultat comme source primaire de PDatum/EDatum
   - Fallback sur `_detect_dates` existant si un item n'est pas trouvé dans le résultat ST

3. **Simplification** :
   - `_compute_pdatum_hint_by_docid_for_page` devient un fallback optionnel (pas le chemin principal)
   - `_search_pdatum_in_content` peut rester comme fallback de dernier recours
   - La logique de hint upgrade/clamp dans `_detect_dates` n'est plus nécessaire pour les items résolus par ST

### Ce qui reste inchangé

- Bootstrap GWT, pagination, tokens
- PDF decryption, barrier sync
- EDatum détection existante (tokens) → reste comme fallback pour les EDatum dédupliqués dans la ST
- Tous les autres cantons

---

## 10. Outils d'analyse

### `tools/extract_pdatum_st.py`

Algorithme standalone pour validation. Usage :

```powershell
# Capturer une page GWT fraîche d'abord (voir tools/fetch_gwt_pages.py), puis :
python tools/extract_pdatum_st.py <gwt_response.txt> [--gt readme/PDatumListe.updated.txt] [--scrape-date 2026-03-05]
```

### `tools/analyze_st_order.py`

Affiche la string table annotée avec le ground truth. Usage :

```powershell
python tools/analyze_st_order.py <gwt_response.txt>
```

### `tools/fetch_gwt_pages.py`

Fetcher standalone pour récupérer des pages GWT fraîches. Usage :

```powershell
python tools/fetch_gwt_pages.py --pages 0-7 --delay 1.5
```

---

## Résumé

| Clé | Valeur |
|-----|--------|
| **Méthode** | Extraction depuis l'ordre de la string table GWT |
| **Précision PDatum** | 98.1% (157/160) sur données fraîches |
| **Précision EDatum** | ~55% (limitation due à la déduplication ST) |
| **Limitation** | Micro-vagues dont le PDatum coïncide avec un EDatum existant |
| **Robustesse** | Testée sur 8 pages × 20 items = 160 items |
| **Implémentation** | `tools/extract_pdatum_st.py` (421 lignes, autonome) |
