# Tribuna Scraper (Scrapy)

Ce projet extrait des décisions/jurisprudences depuis des instances Tribuna via des appels **GWT-RPC** (réponses `//OK[...]`) et télécharge les **PDFs** associés.

Ce README est volontairement **court et opérationnel** : comment lancer, où sont les sorties, et comment est organisé le projet.

## Documentation complète

- **[ALGORITHME_ST_BASED.md](ALGORITHME_ST_BASED.md)** : Algorithme de détection PDatum basé sur la string table GWT — **approche actuelle, 98.1% de précision**
- **[README_LOGIQUE.md](README_LOGIQUE.md)** : Architecture technique détaillée (bootstrap GWT, tokenisation, parsing string table, décryptage PDF, pagination)
- **[README_DATES.md](README_DATES.md)** : Vue d'ensemble de la détection de dates (EDatum + PDatum, mécanismes de fallback)
- **[PDATUM_SLIDING_LIMITATION.md](PDATUM_SLIDING_LIMITATION.md)** : Limitation résiduelle (~1%) due à la déduplication de la string table GWT

## Prérequis

- Windows / Linux
- Python 3.12+
- Scrapy 2.13+ (installé automatiquement)

Ce scraper utilise uniquement des appels HTTP vers des endpoints GWT-RPC. Aucune automation de navigateur (Playwright/Selenium) n'est nécessaire.

## Installation

### Option PowerShell (recommandé)

Depuis `tribuna_scraper/` :

```powershell
./scripts/setup.ps1
```

### Option manuelle

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Vérification :

```powershell
python -m scrapy list
```

## Lancer le spider

### Run complet (1 canton)

```powershell
scrapy crawl tribuna -a canton=schwyz
```

### Run incrémental : `days=N` (1 canton)

`days=N` définit une fenêtre [today-N, today] (inclus).

```powershell
scrapy crawl tribuna -a canton=schwyz -a days=7
```

Selon le canton, le filtre est :
- **server-side** si le template GWT contient `{datum}` (le serveur renvoie déjà la fenêtre)
- **client-side** sinon (stop côté spider quand on passe sous `min_date`)

### Limites utiles (tests)

```powershell
# Limiter le nombre de pages
scrapy crawl tribuna -a canton=bern4 -s MAX_PAGES=2

# Limiter le nombre d'items
scrapy crawl tribuna -a canton=schwyz -s MAX_ITEMS=50
```

## Lancer tous les cantons

```powershell
foreach ($canton in @("schwyz", "graubunden", "jura", "bern1", "bern2", "zug")) {
    scrapy crawl tribuna -a canton=$canton -a days=30
}
```

## Sorties

- Items JSON : `output/publications/publications_<canton>_<timestamp>.json`
- PDFs : `output/pdfs/full/<KANTON_KURZ>/...pdf`

## Structure du projet

- `publication_scraper/spiders/tribuna_spider.py` : Spider multi-cantons (bootstrap GWT, parsing, pagination, decrypt/PDF)
- `publication_scraper/spiders/fribourg_spider.py` : Spider autonome Fribourg (détection PDatum via string table, 98.1% de précision)
- `publication_scraper/spiders/gwt_utils.py` : Utilitaires de parsing GWT-RPC (extraction tokens, string table)
- `publication_scraper/middlewares.py` (`PDFDownloadMiddleware`) : téléchargement et stockage des PDFs
- `publication_scraper/pipelines.py` (`PublicationPipeline`) : écriture JSON/CSV + stats
- `publication_scraper/cantons_config.json` : configuration par canton (URLs, templates GWT, flags)

## Spider Fribourg (standalone)

Le canton de Fribourg dispose d'un spider autonome avec une logique de détection de dates basée sur la **string table GWT** :

```powershell
scrapy crawl fribourg -a days=30      # Incrémental (30 derniers jours)
scrapy crawl fribourg                 # Full run
scrapy crawl fribourg -s MAX_PAGES=3  # Test (3 pages)
```

**Précision PDatum** : 98.1% (algorithme ST-based). Voir [ALGORITHME_ST_BASED.md](ALGORITHME_ST_BASED.md).

## Configuration

Toute la configuration via les arguments Scrapy CLI (`-a key=value`) ou les settings (`-s KEY=VALUE`).
La configuration par canton est dans `publication_scraper/cantons_config.json`.
Aucun fichier `.env` ni variable d'environnement n'est requis.

## Dépannage

Le message `running 'scrapy crawl' with more than one spider` signifie que le nom du spider est manquant.
Utiliser toujours : `scrapy crawl tribuna -a canton=schwyz` (pas juste `scrapy crawl`).
