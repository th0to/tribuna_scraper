# Documentation technique — Scraper un site Tribuna/GWT (avec Scrapy)

Ce document est volontairement **pédagogique et détaillé**. L’objectif n’est pas de “résumer” le projet, mais d’expliquer en detaille comment ce projet fonctionne pour scraper un site construit avec **GWT** (Google Web Toolkit) et des endpoints **GWT-RPC**.

## Documentation associée

- **[README.md](README.md)** : Guide de démarrage rapide (installation, commandes, sorties)
- **[README_DATES.md](README_DATES.md)** : Mécanisme de détection de dates et propagation de marker (pdatum_hint) — LECTURE ESSENTIELLE pour comprendre :
  - Pourquoi certaines dates changent entre les runs
  - Comment le marker garantit la complétude des résultats (37 items vs 32 sans marker)
  - Les trade-offs acceptables (stabilité vs complétude)
  - Tests et preuves empiriques

## Fichiers sources

- spider multi-cantons: [publication_scraper/spiders/tribuna_spider.py](publication_scraper/spiders/tribuna_spider.py)
- **spider Fribourg (standalone)**: [publication_scraper/spiders/fribourg_spider.py](publication_scraper/spiders/fribourg_spider.py)
- parser GWT: [publication_scraper/spiders/gwt_utils.py](publication_scraper/spiders/gwt_utils.py)
- config par canton: [publication_scraper/cantons_config.json](publication_scraper/cantons_config.json)
- stockage PDF: [publication_scraper/middlewares.py](publication_scraper/middlewares.py)
- sorties JSON/CSV: [publication_scraper/pipelines.py](publication_scraper/pipelines.py)

---

## 0) SOMMAIRE

1. Reconnaître un site GWT et identifier les appels RPC utiles.
2. Comprendre les deux “tokens” vitaux de GWT (`X-GWT-Permutation` et `X-GWT-Module-Base`).
3. Lire une réponse `//OK[...]` et comprendre le rôle de la “string table”.
4. Extraire proprement des “lignes” (rows) dans un flux GWT où l’ordre n’est pas explicitement structuré.
5. Mettre en place un scraping stable (pagination, rate limit, retry, barrières de synchronisation).

---

## 1) Le modèle mental: un site GWT, c’est une API déguisée

Sur un site “classique”, la page HTML contient les données. Sur un site GWT, le HTML sert souvent juste de “shell”, et **toutes les actions UI** (chercher, paginer, ouvrir un PDF) passent par des **POST** vers un endpoint RPC.

Dans notre cas:
- la “page de résultats” n’est pas un HTML à parser,
- c’est une **réponse RPC** qui ressemble à du JavaScript: `//OK[ ... ]`.

### Schéma global du pipeline

```
           (1) GET bootstrap
                |
                v
      [headers GWT dynamiques]
                |
                v
           (2) POST loadTable (page N)
                |
                v
         (3) parse //OK[...] -> DocIds + metadata
                |
                +--------------------+
                |                    |
                v                    v
         (4a) PDF direct         (4b) decrypt -> candidates -> PDF
                |                    |
                +---------+----------+
                          v
                 (5) barrière: attendre fin PDFs
                          |
                          v
                   POST loadTable (page N+1)
```

---

## 2) Reconnaissance (méthode générale) — avant d’écrire du code

Quand on intégrez un nouveau canton / un nouveau site GWT, il faut **toujours** commencer par observer le navigateur.

### 2.1 Identifier l’endpoint RPC

Dans l’onglet Network/Reseau:
- filtre sur `loadTable`, `search`, `rpc` ou `tribunavtplus`.
- vous verrez généralement un `POST` avec `Content-Type: text/x-gwt-rpc`.

Ce `POST` est l’appel clé pour récupérer la table de résultats.

Dans notre projet, on le stocke (par canton) sous:
- `result_page_url`

### 2.2 Identifier les headers GWT indispensables

Sur le `POST`, regarde les headers. Les deux importants:

- `X-GWT-Permutation`: “strong name” du client GWT (version/signature).
- `X-GWT-Module-Base`: base URL du module GWT.

Sans eux (ou avec une ancienne valeur), le serveur répond souvent avec une erreur de type “incompatible remote service”.

Dans notre projet, ces valeurs existent en **fallback** dans `cantons_config.json`, mais on préfère les détecter dynamiquement via le bootstrap.

### 2.3 Capturer le body GWT-RPC

Le body n’est pas du JSON. Il ressemble souvent à:

```
7|0|56|https://.../tribunavtplus/|<perm?>|...|search|...|{page_nr}|...|{datum}|...
```

Ce format est typique de GWT-RPC (séparateurs `|`, indices, types Java sérialisés, etc.).

Notre stratégie:
- on conserve ce body sous forme de **template** par canton (avec placeholders `{page_nr}`, parfois `{datum}`),
- le spider remplit les placeholders.

---

## 3) Structure du projet

### 3.1 Pourquoi Scrapy (plutôt qu’un script requests “monolithique”)

Scrapy apporte:
- un moteur de requêtes asynchrone,
- un système de retry, timeouts, stats,
- des middlewares et pipelines,
- une discipline de code (Request -> callback -> items).

Mais attention: GWT + PDFs implique une contrainte “fan-out / join” (on déclenche plein de PDFs puis on attend) → d’où notre **barrière PDF**.

### 3.2 Où sont les responsabilités

- `tribuna_spider.py`: orchestration (bootstrap, pagination, parsing, planification decrypt/PDF)
- `gwt_utils.py`: parsing/heuristiques “bas niveau” sur `//OK[...]`
- `middlewares.py`: sauvegarde des PDFs (détecte la réponse PDF et écrit sur disque)
- `pipelines.py`: écrit les items (JSON/CSV), fait des stats simples
- `cantons_config.json`: les “constantes” par canton (URLs + templates)

---

## 4) Étape A — Bootstrap: récupérer `X-GWT-*` dynamiquement

### 4.1 Pourquoi c’est nécessaire

GWT est souvent déployé avec des “strong names” qui changent lors des releases. Si vous figez `X-GWT-Permutation` en dur, le scraper peut casser du jour au lendemain.

### 4.2 La stratégie

1) GET une page “bootstrap” (souvent la home ou une route `.../?locale=...`).
2) Parser le HTML/JS et extraire:
- `X-GWT-Permutation`
- `X-GWT-Module-Base`
3) Utiliser ces headers sur tous les `POST` RPC suivants.

### 4.3 Fallback

Si le parsing bootstrap échoue:
- on tombe sur les valeurs dans `cantons_config.json`.

Ça évite un arrêt complet, mais c’est moins robuste.

Référence code (bootstrap):
- flow: [`start_requests`](publication_scraper/spiders/tribuna_spider.py#L1028)
- parsing HTML/JS: [`parse_bootstrap`](publication_scraper/spiders/tribuna_spider.py#L1331) et fallback module: [`parse_bootstrap_module`](publication_scraper/spiders/tribuna_spider.py#L1362)
- mise à jour headers: [`_update_gwt_tokens`](publication_scraper/spiders/tribuna_spider.py#L1404)

---

## 5) Étape B — Construire une requête `loadTable`

### 5.1 Full run vs incrémental (`days=N`)

Pour beaucoup de cantons, on a deux templates:
- `result_query_tpl`: full run
- `result_query_tpl_ab`: incrémental

La différence importante: certains cantons ont un vrai filtre date côté serveur.

### 5.2 Le marqueur `{datum}`: la clé “server-side”

Quand `result_query_tpl_ab` contient `{datum}`, c’est un indicateur fort que:
- l’UI envoie un champ date,
- et que le serveur utilise réellement ce champ pour filtrer.

Dans ce cas, le scraper doit surtout:
- **reproduire exactement** le body UI,
- éviter d’appliquer un second filtre local contradictoire.

### 5.3 Pagination

Le placeholder `{page_nr}` pilote la pagination.

Notre règle: on avance page par page, et on ne passe à `page_nr+1` qu’une fois:
- la page N parsée,
- et toutes les requêtes PDF de la page N terminées (barrière).

---

## 6) Étape C — Lire une réponse `//OK[...]` (le point le plus important)

### 6.1 Ce que vous recevez n’est pas du JSON

Les réponses GWT-RPC ressemblent à:

```
//OK[ ... ]
```

À l’intérieur, vous avez une structure de tableaux/valeurs qui **ressemble** à du JavaScript, mais:
- ce n’est pas toujours un JSON strict,
- et il peut y avoir des séquences d’échappement non-standard.

Dans ce projet, le spider convertit surtout la réponse en une liste de “tokens” lisibles via `extract_tokens()`.

`extract_tokens()` essaye d'abord un vrai parse `//OK[...]` (avec string table), puis retombe sur une extraction regex si besoin.

Référence code:
- nettoyage du payload: [`strip_gwt_prefix`](publication_scraper/spiders/gwt_utils.py#L22)
- extraction “tokens”: [`extract_tokens`](publication_scraper/spiders/gwt_utils.py#L360)

### 6.2 La “string table” (table de chaînes)

GWT compresse souvent le payload en mettant les chaînes dans une table, puis en référençant les chaînes par indices.

Exemple simplifié (pédagogique):

```
//OK[
  ["DocId", "2025-12-01", "Titre A"],
  0, 1, 2
]
```

Ici, `0` veut dire “DocId”, `1` veut dire “2025-12-01”, etc.

Dans le vrai monde, c’est plus compliqué: la string table est longue et la structure contient des listes imbriquées.

### 6.3 Notre approche de parsing (en 2 temps)

1) On transforme `//OK[...]` en structure Python (listes/strings/nombres).
2) On localise une “string table” plausible et on “déplie” (inflate) des tokens pour faciliter les heuristiques.

Pourquoi on fait ça?
- parce que l’objectif final est souvent d’extraire des **motifs** (DocId, dates, numéros) dans un flux quasi-linéaire,
- et la représentation “dépliée” aide énormément.

Référence code (parsing GWT robuste):
- parse “vrai” `//OK[...]`: [`parse_ok_array`](publication_scraper/spiders/gwt_utils.py#L141)
- heuristique string table: [`_find_string_table`](publication_scraper/spiders/gwt_utils.py#L161)
- inflation des références: [`_inflate_tokens`](publication_scraper/spiders/gwt_utils.py#L174)

---

## 7) Étape D — Extraire les “lignes” (rows) sans se tromper de DocId

### 7.1 Le piège: le flux n’est pas découpé par ligne

Sur une API JSON, on a un tableau d’objets, chaque objet = une ligne.
Sur GWT-RPC, on a un gros flux où les valeurs de plusieurs lignes peuvent se chevaucher.

### 7.2 Notre stratégie: “DocId-first” + fenêtre locale

1) Détecter tous les `DocId` via une regex (typiquement 32 hex chars).
2) Pour chaque `DocId`, analyser une fenêtre locale de tokens autour.
3) Important: la fenêtre peut contenir plusieurs DocIds → il faut sélectionner le bon.

Illustration:

```
... DocId=A ... titreA ... dateA ... DocId=B ... titreB ... dateB ...
              ^ fenêtre autour de A ^
```

Si on prend “le premier DocId trouvé dans la fenêtre”, on va:
- dupliquer des lignes,
- ou perdre des DocIds.

La logique du spider:
- garde la liste complète des DocIds détectés,
- utilise un indice-hint (position) pour choisir le DocId attendu,
- ne “vole” pas les valeurs de la ligne voisine.

Référence code (DocId-first + fenêtre locale):
- trouver les DocIds dans les tokens: [`_find_docid_indexes`](publication_scraper/spiders/tribuna_spider.py#L512)
- sélectionner le bon DocId dans une fenêtre: [`_select_docid_in_window`](publication_scraper/spiders/tribuna_spider.py#L522)
- extraction “row-local”: [`_extract_row_meta`](publication_scraper/spiders/tribuna_spider.py#L562)
- parsing de la ligne (construction item + planification PDF): [`parse_result_row`](publication_scraper/spiders/tribuna_spider.py#L2398)

---

## 8) Étape E — Extraire des métadonnées (Num, dates, titre…)

### 8.1 Le minimum utile (notre `PublicationItem`)

Une décision doit produire au minimum:
- `DocId`
- `Num` (numéro de dossier)
- `PDatum` (date de publication) quand dispo

Et idéalement:
- `EDatum` (date de décision)
- `Titel`, `Gericht`, `Kammer`, etc.

### 8.2 Dates: “row-local” d’abord, fallback ensuite

**une date trouvée n’est pas forcément la bonne**.

Donc:
1) on cherche d’abord des dates dans la fenêtre locale de la ligne,
2) si on n’a pas de date fiable, on fallback sur un scan “dans le brut” proche du DocId,
3) on rejette des dates futures (ça arrive via du bruit / format).
### 8.1 Le défi Fribourg : dates multiples et ambiguës

**Problème spécifique** : Le site Fribourg mélange plusieurs types de dates :
- **PDatum** (date de publication) : ce qu'on cherche
- **EDatum** (date de décision) : peut être plusieurs mois avant la publication
- **Dates juridiques** : audiences, dépôts, notifications, etc.

Ces dates apparaissent mélangées dans le flux GWT, et il est souvent **impossible de les distinguer sans contexte supplémentaire**.

### 8.2 Solution : le mécanisme de "marker" (pdatum_hint)

**Observation clé** : Les décisions sont publiées par "vagues" (lots) — toutes les décisions d'une même vague ont la **même date de publication**, même si leurs dates de décision varient.

**Idée** : Propager la date de publication détectée d'un item vers les items suivants.

**Implémentation** : Un système de "marker" qui :
1. Scanne le contenu GWT de gauche à droite
2. Quand un chunk contient **2+ dates distinctes**, crée un marker = date MAX
3. Propage ce marker aux DocIds suivants jusqu'à ce qu'un nouveau marker apparaisse
4. Applique des règles intelligentes (upgrade, clamp, rescue) pour utiliser le hint

**Résultats empiriques** (tests réels) :
- ✅ **Avec marker** : 37 items capturés (100%)
- ❌ **Sans marker** : 32 items capturés (86%) → perte de 5 items (-13%)
- ❌ **Sans marker** : 8 items avec des dates erronées (juridiques ou fallback)

**Documentation complète** : Voir [README_DATES.md](README_DATES.md) pour :
- Le problème en détail (dates multiples et ambiguës)
- L'algorithme complet de propagation de marker
- Les règles d'application (upgrade/clamp/rescue)
- Les tests et preuves d'efficacité
- Les défauts connus et trade-offs acceptables
- Les recommandations d'usage en production

**Conclusion** : Le marker est **indispensable** pour Fribourg. Les bénéfices (complétude, robustesse) surpassent largement les inconvénients (dates qui peuvent changer entre runs).
---

## 9) Étape F — Récupérer le PDF (direct vs decrypt)

### 9.1 Cas 1: URL directe (pfad)

Parfois le flux contient un chemin exploitable (“pfad”).
Dans ce cas:
- on construit une URL de download,
- on déclenche une requête GET PDF.

### 9.2 Cas 2: “decrypt” (très fréquent)

Souvent, le flux contient un token chiffré/opaque.
Alors:
1) on fait un POST RPC sur `decrypt_page_url`,
2) on parse la réponse,
3) on génère plusieurs URLs candidates,
4) on teste les candidates.

Validation PDF:
- on accepte `HTTP 200` et `HTTP 206` (partial content),
- on vérifie `Content-Type` contient `pdf` (ou suffixe `.pdf`).

Même si un PDF échoue:
- l’item est quand même émis,
- et on incrémente un compteur (utile pour monitoring).

### 9.3 Où le PDF est sauvegardé

Le download est géré par `PDFDownloadMiddleware` (middleware downloader) qui:
- détecte si la réponse “ressemble” à un PDF,
- déduit un nom de fichier (DocId > Num > nom URL),
- écrit sous `FILES_STORE/full/<KANTON>/...pdf`.

Référence code (PDF / decrypt):
- détection `pfad` dans le contenu: [`_detect_pdf_path`](publication_scraper/spiders/tribuna_spider.py#L772) (et helper [`_search_pdf_path_in_content`](publication_scraper/spiders/tribuna_spider.py#L732))
- construction candidates decrypt: [`_build_pdf_candidates_from_decrypt`](publication_scraper/spiders/tribuna_spider.py#L985) et callback decrypt: [`decrypt_path`](publication_scraper/spiders/tribuna_spider.py#L2595)
- validation candidate PDF: [`verify_pdf_candidate`](publication_scraper/spiders/tribuna_spider.py#L2720) et règle `pdf ok`: [`_is_pdf_ok`](publication_scraper/spiders/tribuna_spider.py#L999)
- errbacks: [`errback_decrypt`](publication_scraper/spiders/tribuna_spider.py#L2871), [`errback_pdf_candidate`](publication_scraper/spiders/tribuna_spider.py#L2916)
- écriture sur disque: [`PDFDownloadMiddleware`](publication_scraper/middlewares.py#L9) / [`process_response`](publication_scraper/middlewares.py#L30)

---

## 10) La “barrière PDF”: un pattern indispensable

### 10.1 Pourquoi une barrière

Sans barrière, Scrapy pourrait:
- déclencher page 2, page 3, etc.
- pendant que les PDFs page 1 ne sont pas finis,

…et on perd le contrôle (charge serveur, logique métier, ou dépendances de parsing).

### 10.2 Le pattern “fan-out / join”

Sur une page N:
- fan-out: on planifie toutes les requêtes decrypt/PDF,
- join: on attend que toutes soient terminées,
- puis seulement on planifie la page N+1.

Si vous concevez des scrapers GWT, retenez ce pattern: il revient souvent (PDF, détails, pièces jointes, endpoints secondaires).

Référence code (barrière fan-out/join):
- activation/config: `PAGE_PDF_BARRIER` ([tribuna_spider.py#L422](publication_scraper/spiders/tribuna_spider.py#L422))
- register/done: [`_barrier_register`](publication_scraper/spiders/tribuna_spider.py#L436) et [`_barrier_done`](publication_scraper/spiders/tribuna_spider.py#L459)
- blocage pagination (log "Barrier active..."): [tribuna_spider.py#L2368](publication_scraper/spiders/tribuna_spider.py#L2368)

---

## 11) Mode incrémental `days=N`: deux modes, une seule interface

On expose `days=N` pour obtenir une fenêtre temporelle.

Définition:
- `min_date = today - N`
- `max_date = today`

### 11.1 Mode A — filtre côté serveur

Signal: le template contient `{datum}`.

Dans ce mode:
- le serveur est censé renvoyer des résultats déjà filtrés,
- on s’attend à ce que la page contienne majoritairement des DocIds “éligibles”.

### 11.2 Mode B — filtre côté client

Quand il n’y a pas `{datum}`:
- on récupère des pages triées (souvent par publication),
- et on “stoppe” quand on est suffisamment dans le passé.

Ce stop est une heuristique (configurable), parce que certains portails mélangent des anciennes/publications “hors tri parfait”.

Référence code (days=N):
- initialisation `days/min_date/max_date`: [`__init__`](publication_scraper/spiders/tribuna_spider.py#L133)
- application du cutoff + règles d’arrêt: [`parse_page`](publication_scraper/spiders/tribuna_spider.py#L1533) (voir aussi la sélection cutoff [tribuna_spider.py#L2043](publication_scraper/spiders/tribuna_spider.py#L2043) et la condition d’arrêt [tribuna_spider.py#L2258](publication_scraper/spiders/tribuna_spider.py#L2258))

---

## 12) `STRICT_FULL`: à quoi ça sert (et comment l’activer)

`STRICT_FULL` est un mode de robustesse/complétude, activé par défaut via la config du projet.

Objectif:
- éviter des raccourcis qui diminuent silencieusement la couverture,
- détecter rapidement des régressions (ex: DocIds détectés mais non-yieldés).

Override ponctuel (PowerShell):

```powershell
$env:STRICT_FULL = "0"
python -m scrapy crawl tribuna -a canton=schwyz
```

Note: l’erreur `running 'scrapy crawl' with more than one spider` survient typiquement si on passe des arguments positionnels inattendus, par ex. `scrapy crawl tribuna STRICT_FULL=0` (au lieu de `-s` ou env).

---

## 13) Debug: comment diagnostiquer un site GWT qui casse

### 13.1 Symptômes fréquents

1) Le serveur renvoie une erreur d’incompatibilité (souvent permutation invalide)
- → vérifier `X-GWT-Permutation` et le bootstrap.

2) Beaucoup de DocIds détectés mais peu d’items
- → bug de “fenêtre” / sélection du bon DocId.

3) PDFs qui échouent en masse
- → decrypt cassé, endpoints candidats changés, ou règles anti-bot.

### 13.2 Dumps utiles

Le projet expose des flags de dump (via variables d’environnement) pour:
- comparer le body généré par le spider à celui observé dans l’UI,
- inspecter les réponses `//OK[...]` en brut.

Exemple (PowerShell):

```powershell
$env:DUMP_GWT_BODY = "1"
$env:DUMP_GWT_RESPONSE = "1"
python -m scrapy crawl tribuna -a canton=bern2 -a days=30
```

---

## 14) Ajouter un nouveau canton: checklist reproductible

1) Observer le `POST loadTable` dans le navigateur.
2) Copier l’URL vers `result_page_url` et le body vers `result_query_tpl`.
3) Si l’UI envoie un filtre date: ajouter `result_query_tpl_ab` et le placeholder `{datum}`.
4) Vérifier `X-GWT-Module-Base` et `X-GWT-Permutation`:
   - les mettre en fallback dans `headers`,
   - mais s’assurer que le bootstrap peut les découvrir.
5) Lancer un test rapide:

```powershell
python -m scrapy crawl tribuna -a canton=<nouveau_canton> -s MAX_PAGES=1
```

6) Contrôler:
- DocIds détectés vs items émis,
- présence de PDFs,
- absence de boucles/pagination infinie.

---

## 15) Références code (pour relier théorie → implémentation)

Cette section sert d’index “je lis → j’ouvre le code”.

- Bootstrap GWT:
  - [`start_requests`](publication_scraper/spiders/tribuna_spider.py#L1028)
  - [`parse_bootstrap`](publication_scraper/spiders/tribuna_spider.py#L1331)
  - [`parse_bootstrap_module`](publication_scraper/spiders/tribuna_spider.py#L1362)
  - [`_update_gwt_tokens`](publication_scraper/spiders/tribuna_spider.py#L1404)

- Requête résultats + parsing page:
  - pagination: [`get_next_request`](publication_scraper/spiders/tribuna_spider.py#L1206)
  - parsing principal: [`parse_page`](publication_scraper/spiders/tribuna_spider.py#L1533)

- Parsing GWT “bas niveau”:
  - nettoyer le préfixe: [`strip_gwt_prefix`](publication_scraper/spiders/gwt_utils.py#L22)
  - tokens (parse + fallback): [`extract_tokens`](publication_scraper/spiders/gwt_utils.py#L360)
  - parse `//OK[...]`: [`parse_ok_array`](publication_scraper/spiders/gwt_utils.py#L141)

- Rows (DocId-first):
  - DocId indexes: [`_find_docid_indexes`](publication_scraper/spiders/tribuna_spider.py#L512)
  - sélection DocId dans la fenêtre: [`_select_docid_in_window`](publication_scraper/spiders/tribuna_spider.py#L522)
  - extraction row-local: [`_extract_row_meta`](publication_scraper/spiders/tribuna_spider.py#L562)
  - parsing ligne: [`parse_result_row`](publication_scraper/spiders/tribuna_spider.py#L2398)

- PDF / decrypt:
  - decrypt callback: [`decrypt_path`](publication_scraper/spiders/tribuna_spider.py#L2595)
  - vérification candidate: [`verify_pdf_candidate`](publication_scraper/spiders/tribuna_spider.py#L2720)
  - middleware d’écriture PDF: [`PDFDownloadMiddleware`](publication_scraper/middlewares.py#L9)

- Config canton:
  - [publication_scraper/cantons_config.json](publication_scraper/cantons_config.json)
