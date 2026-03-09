# PDatum Sliding Problem — Why It Cannot Be Fixed

**Document Status**: Final Analysis — mise à jour 05.03.2026
**Algorithme actuel** : ST-based — 98.1 % de précision (157/160)
**Limitation résiduelle** : ~1.9 % des items (3/160) ont un PDatum incorrect

> **Contexte historique** : Ce document a été rédigé initialement lors de l’analyse de l’approche marker propagation (90.9 %, 4/44 erreurs). Depuis mars 2026, l’algorithme ST-based réduit les erreurs à ~1.9 %. La cause structurelle documentée ici demeure valide — elle explique pourquoi ce résidu ne peut pas être supprimé.

---

## TL;DR

Certains items obtiennent un PDatum incorrect parce que **la date correcte n'existe pas de façon non ambiguë** dans la réponse GWT-RPC pour ces DocIds. C'est une limitation de disponibilité des données (déduplication côté serveur), pas un bug de code. Toutes les tentatives de correction empiriques ont dégradé la précision globale.

---

## The 4 Problem Items

| Nummer | DocId (16 chars) | Expected | Got | Gap |
|--------|-----------------|----------|-----|-----|
| 601 2026 17 | `575032d4f3d94513` | 2026-02-10 | 2026-02-11 | +1 day |
| 501 2025 82 | `8a52e1f2c23448b0` | 2026-01-13 | 2026-01-20 | +7 days |
| 501 2025 63 | `bf0b785f6a8c488d` | 2026-01-13 | 2026-01-20 | +7 days |
| 501 2025 9  | `201547ccbb63498c` | 2026-01-13 | 2026-01-20 | +7 days |

---

## Forensic Analysis: What Actually Happens

### Case 1: Item 601 2026 17 (02-10 → 02-11)

**Ground Truth** (from UI manual extraction):
- PDatum: 2026-02-10
- EDatum: 2026-02-09

**What the spider sees in GWT data**:
```
Page 0 content (8365 chars, 20 DocIds):
  DocId [0] b7b2fd47 at pos=174
    - has dates: [2026-02-10@285, 2026-02-13@661]
    - 2026-02-10 is THIS item's PDatum
  
  DocId [5] 575032d4 at pos=1835  <-- Problem item
    - has dates: [2026-02-09@2249]
    - 2026-02-09 is EDatum (correctly detected)
    - 2026-02-10 is at position 285, which is 1550 chars BEFORE this DocId
    - Search window: 800 chars before + until next DocId
    - 2026-02-10 is OUTSIDE the window (belongs to b7b2fd47's chunk)
```

**Result**:
1. Raw content scan returns `None` (no date found in window)
2. Hint mechanism rescues with `2026-02-11` (propagated from other items)
3. Final PDatum = **2026-02-11** ❌

**Why 2026-02-10 is unreachable**:
- It's 1857 chars away (in another item's chunk)
- Expanding search window to 2000 chars would capture wrong dates from multiple items
- The GWT server doesn't repeat PDatum for every item

---

### Case 2: Items 501 2025 82/63/9 (01-13 → 01-20)

**Ground Truth**:
- PDatum: 2026-01-13 for all 3 items
- EDatum: 2025-11-07, 2025-11-24, 2025-12-01

**What the spider sees in GWT data**:
```
Page 1 content (8572 chars, 20 DocIds):
  DocId [13] ac9c926c at pos=5502
    - has dates: [2025-11-20, 2026-01-21]
    - marker updates to 01-21
  
  DocId [14] c87ee9ee at pos=6037
    - has dates: [2025-12-30, 2026-01-20]
    - marker updates to 01-20
  
  DocId [17] 8a52e1f2 at pos=7383  <-- Problem item
  DocId [18] bf0b785f at pos=7769  <-- Problem item
  DocId [19] 201547cc at pos=8058  <-- Problem item
    - have dates: only their EDatums (2025-11-07, 2025-11-24, 2025-12-01)
    - NO 2026-01-13 anywhere in their content windows
    - The only "2026-01-13" on the page is at position 3060
    - That's the EDatum of item 04e8dbe0 ("605 2025 158")
    - Distance: 4300+ chars from first problem item
```

**Result for 8a52e1f2**:
1. Raw content scan finds old dates: `2026-01-05`, `2025-11-12`, `2025-11-07`
2. Best candidate: `2026-01-05` (most recent)
3. Hint: `2026-01-20` (propagated marker)
4. Gap: 15 days > 5 days → upgrade condition triggers
5. Final PDatum = **2026-01-20** ❌

**Why 2026-01-13 is unreachable**:
- It doesn't exist as a PDatum token near these DocIds
- The only occurrence is 4300+ chars away, as another item's EDatum
- The GWT server presents mixed "publication waves" on the same page

---

## Why All Fix Attempts Failed

### Experiment 1: Disable Hint Upgrade

**Hypothesis**: If we stop the hint from upgrading local dates, the 4 items will keep their correct dates.

**Implementation**:
```python
# BEFORE
elif hint_d > cur_d:
    apply_hint = True  # Upgrade

# AFTER (test)
elif hint_d > cur_d:
    apply_hint = False  # Never upgrade
```

**Result**: **CATASTROPHIC**
- Accuracy: 40/44 → **31/44 (70.5%)**
- 13 items that depended on upgrade to fix wrong dates **broke**
- Net loss: Fixed 4, broke 13 = -9 items

**Why it failed**: The upgrade correctly fixes many items where raw content finds:
- EDatum instead of PDatum
- "Juridical dates" (audience dates, filing dates)
- Dates from wrong sections

---

### Experiment 2: Increase Upgrade Threshold (>5 days → >10 days)

**Hypothesis**: Only upgrade if gap is large (>10 days), preserving small gaps.

**Result**: **8 regressions**
- Items with legitimate 6-9 day gaps lost their corrections
- Accuracy dropped to ~73%

---

### Experiment 3: Expand Content Search Window (800 → 2000 chars)

**Hypothesis**: Larger window catches the distant correct dates.

**Problem**:
- Wider window captures dates from **other items' chunks**
- Scoring favors "most recent" → picks wrong dates from nearby items
- Creates more contamination than it solves

---

### Experiment 4: Disable Hint Completely

**Result**: ~80% of items would have **no PDatum at all**
- Most items don't have PDatum as a visible token
- Hint provides the only source of PDatum for majority of items

---

## The Fundamental Trade-off

```
Hint Upgrade Benefits: ~13 items
  - Corrects EDatum confused with PDatum
  - Corrects juridical dates
  - Rescues items below min_date threshold

Hint Upgrade Costs: 4 items
  - Overwrites correct local dates when hint is from different wave

Net Impact: +9 items correct
```

**Mathematical fact**: No threshold value (5, 7, 10, 15 days) exists that fixes the 4 wrong items without breaking 8+ currently correct items.

---

## Why This Is Architecturally Unfixable

### Root Cause: GWT Server Data Structure

The GWT-RPC server sends responses where:
1. PDatum appears as "section headers" far from some DocIds
2. Multiple "publication waves" are mixed on the same page
3. There's no explicit tagging of which date belongs to which item

**The server presents**:
```
[Section: 2026-02-10]
  Item A (full data)
  Item B (full data)
  
[Section: 2026-02-11]
  Item C (full data)
  Item D (full data)
  Item E (partial data, section date far away)  ← Problem item
```

**What we need but don't have**:
```json
{
  "items": [
    {"docid": "575032d4", "pdatum": "2026-02-10", ...},  // Explicit!
    {"docid": "8a52e1f2", "pdatum": "2026-01-13", ...}
  ]
}
```

### Why Machine Learning Won't Help

Even with ML/heuristics to detect "section boundaries", we'd need:
- Training data showing which dates belong to which DocIds
- But we don't have correct labels for all items (that's the problem!)
- And the server data structure varies per page

---

## Solutions That Would Require Major Changes

### Option A: Manual Correction Table

Maintain a hardcoded map:
```python
KNOWN_CORRECTIONS = {
    "575032d4f3d94513": "2026-02-10",
    "8a52e1f2c23448b0": "2026-01-13",
    # ...
}
```

**Problems**:
- Breaks on new data
- Doesn't generalize
- Maintenance nightmare

---

### Option B: Two-Pass Extraction

1. First pass: Get all DocIds from UI with correct PDatum
2. Second pass: Use scraped data, inject UI dates

**Problems**:
- Requires browser automation (Playwright)
- Defeats purpose of pure API scraping
- Much slower

---

### Option C: Request Server API Changes

Ask Tribuna to provide structured JSON with explicit PDatum per item.

**Reality**: Government IT projects take years, if approved at all.

---

## Statut accepté

**Algorithme actuel** : ST-based (depuis mars 2026)
**Précision** : 98.1 % (157/160 corrects)
**Erreurs résiduelles** : ~3 items (~1.9 %)
**Cause** : Déduplication GWT côté serveur — limitation structurelle
**Correctif disponible** : Aucun sans modification côté serveur

C'est le **meilleur résultat atteignable** avec l’architecture actuelle et la structure de données GWT.

---

## Commandes de validation

```powershell
# Lancer le spider
scrapy crawl fribourg -a days=34

# Valider la précision
python tools/validate_accuracy.py output/<dernier_run>.json

# Résultat attendu (ST-based):
# === Accuracy: 157/160 matched (98.1%) ===
```

---

## Documentation associée

- [ALGORITHME_ST_BASED.md](ALGORITHME_ST_BASED.md) — Spécification complète de l’algorithme actuel
- [README_DATES.md](README_DATES.md) — Vue d’ensemble de la détection de dates

---

**Conclusion** : Le résidu ~1.9 % d’erreurs est une **limitation acceptée**. Le PDatum correct n’existe tout simplement pas de façon non ambiguë dans les données GWT pour ces items. L’algorithme ST-based est le meilleur équilibre atteignable sans modification serveur.
