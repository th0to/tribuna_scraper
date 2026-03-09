# Détection de dates — Spider Fribourg

**Dernière mise à jour** : 2026-03-05
**Statut** : ✅ Stable — 98.1 % de précision (157/160 items)
**Algorithme** : ST-based (extraction depuis la string table GWT)
**Limitation résiduelle** : ~1 % des items (déduplication GWT). Voir [PDATUM_SLIDING_LIMITATION.md](PDATUM_SLIDING_LIMITATION.md)

---

## Vue d'ensemble

Le spider Fribourg extrait **deux dates** par décision judiciaire :

- **EDatum** (Entscheidungsdatum) : date de la décision du tribunal
- **PDatum** (Publikationsdatum) : date de publication sur le site **← champ clé pour le filtrage incrémental**

Le PDatum est utilisé pour le mode `days=N` : seules les décisions publiées dans les N derniers jours sont restituées.

---

## Algorithme actuel : ST-based (depuis mars 2026)

L'approche repose sur l'**ordre de la string table** dans les réponses GWT-RPC.

### Principe clé

> Dans une réponse GWT, la string table est un tableau de chaînes uniques déduplicatées, référencées par indices numériques. L'ordre dans ce tableau reflète l'ordre d'apparition des items dans l'UI.

Les décisions judiciaires apparaissent séquentiellement dans la string table. Les dates de publication (PDatum) se trouvent dans les **zones inter-items**, entre les blocs DOCID de deux décisions consécutives.

### Classification des entrées

Chaque entrée de la string table est classifiée :

| Classe | Exemple | Critère |
|--------|---------|---------|
| `DOCID` | `a3f2c1d4...` (32 hex) | Identifiant hexadécimal 32 chars |
| `NUM` | `101 2024 123` | Pattern numéro de dossier |
| `DATE` | `2026-03-05` | Format ISO YYYY-MM-DD valide |
| `SEP` | `\\` ou `|` | Séparateur |
| `GCODE` | `FR` | Code géographique 2 chars |

### Règles d'extraction

**EDatum** : entrée DATE à l'indice `ST[NUM_idx + 1]` (règle de position fixe par rapport au NUM).

**PDatum** :
1. Identifier la zone inter-items : `ST[DocId_i+1 .. DocId_i+1 - 1]`
2. Conserver les DATE dans cette zone
3. Appliquer le critère de **décroissance stricte monotone** : la séquence de PDatum doit être non-croissante au fil des items

### Performance

| Run | Items | PDatum corrects | Précision |
|-----|-------|-----------------|-----------|
| 05.03.2026 (8 pages) | 160 | 157 | **98.1 %** |

La documentation complète de l'algorithme se trouve dans [ALGORITHME_ST_BASED.md](ALGORITHME_ST_BASED.md).

---

## Détection EDatum (détail)

EDatum est encodé à une **position fixe** dans les tokens GWT, relative au DocId :

```python
edatum = tokens[id_pos + 3]  # Format : YYYY-MM-DD
```

Pour les cas où EDatum est dédupliqué dans la string table (même date partagée par plusieurs items consécutifs), le fallback positionnel `tokens[id_pos + 3]` est appliqué.

---

## Limitation résiduelle (~1 %)

3 items sur 160 (~1.9 %) ont un PDatum incorrect. La cause est structurelle :

- Le GWT server déduplique les chaînes dans la string table : si deux items consécutifs partagent le même PDatum, ce PDatum n'apparaît **qu'une seule fois** dans la string table.
- Dans les micro-vagues (1 ou 2 items avec un PDatum distinct entouré d'autres vagues), la déduplication peut fusionner des entrées de la zone inter-items voisine, rendant le PDatum localement indétectable.

**Ce n'est pas un bug de code** — l'information n'est tout simplement pas présente de façon unambiguë dans les données serveur.

Analyse complète : [PDATUM_SLIDING_LIMITATION.md](PDATUM_SLIDING_LIMITATION.md)

---

## Mécanisme de fallback (marker propagation)

Avant l'algorithme ST-based, le spider utilisait une **propagation de marqueur** (PDatum propagé par vague). Ce mécanisme reste disponible comme fallback de dernier recours pour les items non résolus par la ST.

### Fonctionnement (résumé)

1. **Calcul du marker** : `max()` des dates distinctes d'un chunk de page
2. **Propagation** : le marker se propage aux items suivants (décroissance monotone)
3. **Application** : rescue (pas de date locale) ou upgrade (si conditions spécifiques)

**Précision du fallback seul** : ~83–90 % selon les pages. Désactiver le fallback ST-based au profit du marker seul dégrade significativement les résultats.

---

## Configuration

```json
// fribourg_config.json
{
  "pdatum_future_days": 0,
  "edatum_future_days": 14
}
```

- `pdatum_future_days: 0` — PDatum ne peut pas être dans le futur
- `edatum_future_days: 14` — EDatum peut être jusqu'à +14 j (arrêts urgents)

### Mode incrémental

```bash
scrapy crawl fribourg -a days=30
```

Filtre : `PDatum >= today - 30 jours`

---

## Débogage

### Activer les traces

```bash
DEBUG_TRACE=1 scrapy crawl fribourg -a days=7
```

Traces sauvegardées dans `~/.tribuna_traces/fribourg/{docid}.json`.

### Valider la précision

```bash
python tools/validate_accuracy.py output/<dernier_run>.json
```

Ground truth : `readme/PDatumListe.updated.txt` (mis à jour le 05.03.2026)

### Analyser la string table

```bash
python tools/analyze_st_order.py <gwt_response.txt>
python tools/extract_pdatum_st.py <gwt_response.txt> --gt readme/PDatumListe.updated.txt
```

---

## Références code

| Fichier | Lignes | Rôle |
|---------|--------|------|
| `fribourg_spider.py` | ~713–900 | `_detect_dates()` — orchestration principale |
| `fribourg_spider.py` | ~587–698 | `_search_pdatum_in_content()` — fallback content scan |
| `fribourg_spider.py` | ~460–580 | `_compute_pdatum_hint_by_docid_for_page()` — calcul marker |
| `lib/date_detector.py` | entier | Classe DateDetector (stratégie last_in_row) |
| `tools/extract_pdatum_st.py` | entier | Outil standalone de validation ST-based |

---

**Voir aussi** : [ALGORITHME_ST_BASED.md](ALGORITHME_ST_BASED.md) pour la spécification complète de l'algorithme ST-based.
