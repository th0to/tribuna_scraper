# Tribuna Scraper (Scrapy)

Ce projet extrait des décisions/jurisprudences depuis des instances Tribuna via des appels **GWT-RPC** (réponses `//OK[...]`) et télécharge les **PDFs** associés.

Ce README est volontairement **court et opérationnel**: comment lancer, où sont les sorties, et comment est organisé le projet.

## Documentation complète

- **[README_LOGIQUE.md](README_LOGIQUE.md)** : Architecture technique détaillée (bootstrap GWT, tokenisation, parsing "string table", décryptage PDF, pagination, etc.)
- **[README_DATES.md](README_DATES.md)** : Mécanisme de détection de dates et propagation de marker (pdatum_hint) - ESSENTIEL pour comprendre pourquoi certaines dates changent entre les runs et comment le système garantit la complétude des résultats

## Prérequis

- Windows / Linux
- Python 3.12+

## Installation

### Option PowerShell (recommandé)

Depuis `tribuna_scraper/`:

```powershell
./scripts/setup.ps1
```

### Option manuelle

```powershell
python -m venv .venv
\.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Vérification:

```powershell
python -m scrapy list
```

## Lancer le spider

### Run normal (1 canton)

Full run = historique complet du canton.

```powershell
python -m scrapy crawl tribuna -a canton=schwyz
```

### Run incrémental: `days=N` (1 canton)

`days=N` définit une fenêtre $[today-N, today]$ (inclus).

```powershell
python -m scrapy crawl tribuna -a canton=schwyz -a days=7
```

Selon le canton, le filtre est:
- **server-side** si le template GWT contient `{datum}` (le serveur renvoie déjà la fenêtre)
- **client-side** sinon (tri/stop côté spider quand on passe sous `min_date`)

### Mode strict (par défaut)

`STRICT_FULL=1` est activé par défaut.

Désactivation ponctuelle:

```powershell
python -m scrapy crawl tribuna -a canton=graubunden -s STRICT_FULL= 0
```

### Limites utiles (tests)

```powershell
# limiter le nombre de pages (0 = illimité)
python -m scrapy crawl tribuna -a canton=bern4 -s MAX_PAGES=2

# limiter le nombre d’items
python -m scrapy crawl tribuna -a canton=schwyz -s MAX_ITEMS=50
```

## Lancer tous les cantons (runner)

Le runner `run_all_cantons.py` lance le spider canton par canton via `subprocess` et écrit un rapport récapitulatif.

### Full run (tous les cantons)

```powershell
python .\run_all_cantons.py
```

### Incrémental (tous les cantons)

```powershell
python .\run_all_cantons.py --days 30 --max-pages 4
```

### Limiter un run du runner (tests)

```powershell
# Ne lancer que certains cantons (séparés par virgule)
python .\run_all_cantons.py --cantons schwyz,graubunden,jura --days 7 --max-pages 1

# Désactiver ponctuellement le mode strict
python .\run_all_cantons.py --days 7 --max-pages 1 --no-strict-full
```

## Sorties

- Items JSON: `output/publications/publications_<canton>_<timestamp>.json`
- PDFs: `output/pdfs/full/<KANTON_KURZ>/...pdf`
- Dumps debug (optionnels): `output/gwt_bodies/`, `output/gwt_responses/`, `publication_scraper/output/debug_decrypts/`

## Structure du projet

- `publication_scraper/spiders/tribuna_spider.py`: Spider multi-cantons (bootstrap GWT, parsing, pagination, decrypt/PDF)
- `publication_scraper/spiders/fribourg_spider.py`: Spider autonome Fribourg avec mécanisme de propagation de marker (pdatum_hint)
- `publication_scraper/spiders/gwt_utils.py`: Utilitaires de parsing GWT-RPC (extraction tokens, construction URLs PDF)
- `publication_scraper/middlewares.py` (`PDFDownloadMiddleware`): téléchargement et stockage des PDFs
- `publication_scraper/pipelines.py` (`PublicationPipeline`): écriture JSON/CSV + stats
- `publication_scraper/cantons_config.json`: configuration par canton (URLs, templates GWT, flags)

## Spider Fribourg (standalone)

Le canton de Fribourg dispose d'un spider autonome avec une logique de détection de dates spécifique:

```powershell
# Run incrémental (30 derniers jours)
scrapy crawl fribourg -a days=30

# Full run
scrapy crawl fribourg

# Avec limite de pages (tests)
scrapy crawl fribourg -a days=30 -s MAX_PAGES=3
```

**Important**: Le spider Fribourg utilise un mécanisme de **propagation de marker** qui garantit la complétude des résultats. Voir [README_DATES.md](README_DATES.md) pour les détails.

## Configuration (variables d’environnement)

Le projet charge automatiquement un fichier `.env` (si présent) et lit aussi les variables d’environnement.
Les paramètres importants sont documentés dans `.env.example`.

## Dépannage

Si tu vois `running 'scrapy crawl' with more than one spider`, c’est généralement parce que la commande n’a pas reçu le **nom du spider**.

Exemple correct:

```powershell
python -m scrapy crawl tribuna -a canton=schwyz
```
