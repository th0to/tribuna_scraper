Tribuna Scraper
Scraper Scrapy moderne et réutilisable pour les portails Tribuna des tribunaux cantonaux suisses.
🎯 Objectifs

Collecte automatisée des décisions de justice publiées sur les portails Tribuna
Architecture modulaire facilement extensible pour nouveaux cantons
Extraction robuste avec heuristiques multilingues (FR/DE/IT)
Export structuré en NDJSON avec téléchargement des PDFs

🚀 Installation rapide
Avec Poetry (recommandé)
bash# Cloner le repo
git clone https://github.com/entscheidsuche/tribuna-scraper.git
cd tribuna-scraper

# Installer avec Poetry
poetry install

# Activer l'environnement
poetry shell
Avec pip
bash# Créer environnement virtuel
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows

# Installer dépendances
pip install -r requirements.txt
📦 Structure du projet
tribuna_scraper/
├── spiders/
│   ├── base_tribuna.py    # Spider générique avec heuristiques
│   └── fribourg.py         # Implementation Fribourg
├── utils/
│   ├── normalizers.py      # Normalisation dates, parsing URLs
│   └── parsers.py          # Parsing spécifiques
├── pipelines.py            # Validation, dedup, téléchargement PDF
├── items.py                # Modèle de données
└── settings.py             # Configuration Scrapy
🔧 Utilisation
Lancer le scraper Fribourg
bash# Scraper simple
scrapy crawl fribourg

# Avec export JSONL
scrapy crawl fribourg -o output/fribourg.jsonl

# Mode debug avec cache HTTP
scrapy crawl fribourg -s HTTPCACHE_ENABLED=True
Ajouter un nouveau canton

Créer un nouveau spider héritant de BaseTribunaSpider:

python# tribuna_scraper/spiders/vaud.py
from .base_tribuna import BaseTribunaSpider

class VaudSpider(BaseTribunaSpider):
    name = 'vaud'
    allowed_domains = ['vd.ch']
    start_urls = ['https://www.vd.ch/tribunaux/']
    
    # Optionnel: override pour spécificités locales
    def parse_decisions_list(self, response):
        # Logique spécifique si nécessaire
        yield from super().parse_decisions_list(response)

Lancer le nouveau spider:

bashscrapy crawl vaud
📊 Format de sortie (NDJSON)
Chaque ligne du fichier JSONL contient:
json{
  "source": "publicationtc.fr.ch",
  "locale": "fr",
  "publication_date": "2025-06-04",
  "decision_date": "2025-06-04",
  "docket_number": "101_2025_26",
  "court": "IIe Cour d'appel civil",
  "title": "Arrêt du 4 juin 2025",
  "detail_url": "https://publicationtc.fr.ch/?locale=fr",
  "pdf_url": "https://publicationtc.fr.ch/tribunavtplus/ServletDownload/101_2025_26.pdf?...",
  "pdf_id": "101_2025_26_a0f56a97c2d648c39aa521b0f661ff9f",
  "pdf_params": {
    "dossiernummer": "101_2025_26",
    "path": "...",
    "pathIsEncrypted": true
  },
  "pdf_sha256": "abcd1234...",
  "pdf_path": "downloads/publicationtc.fr.ch/2025-06-04/101_2025_26.pdf",
  "scraped_at": "2025-09-16T14:30:00+01:00"
}
🧪 Tests
bash# Lancer tous les tests
pytest

# Tests avec coverage
pytest --cov=tribuna_scraper

# Test spécifique
pytest tests/test_fribourg_parser.py -v
⚙️ Configuration avancée
Settings personnalisés
python# custom_settings.py
CUSTOM_SETTINGS = {
    'DOWNLOAD_DELAY': 2,
    'CONCURRENT_REQUESTS': 1,
    'AUTOTHROTTLE_ENABLED': True,
    'FILES_STORE': 's3://my-bucket/pdfs',  # Stockage S3
    'FEED_EXPORT_ENCODING': 'utf-8',
}

# Utilisation
scrapy crawl fribourg -s DOWNLOAD_DELAY=3
Pipeline personnalisé
python# Ajouter dans pipelines.py
class CustomDatabasePipeline:
    def process_item(self, item, spider):
        # Insérer dans PostgreSQL/MongoDB
        return item

# Activer dans settings.py
ITEM_PIPELINES['CustomDatabasePipeline'] = 500
🔍 Cas difficiles (GWT-RPC)
Si un portail utilise GWT-RPC sans liens directs:

Identifier les requêtes RPC dans DevTools
Sauvegarder HAR pour analyse
Utiliser le fallback dans le spider:

pythonclass DifficultSpider(BaseTribunaSpider):
    def parse(self, response):
        # Essayer export direct d'abord
        if export_url := self.find_export_links(response):
            yield Request(export_url, self.parse_export)
        else:
            # Fallback: émuler RPC ou crawler pattern ServletDownload
            yield from self.crawl_servlet_patterns(response)
📋 Liste des portails supportés
CantonURLSpiderStatusFribourgpublicationtc.fr.chfribourg✅ ActifVaudvd.ch/tribunauxvaud🚧 En devGenèvege.ch/justicegeneve📋 PlanifiéZürich......📋 Planifié
🐛 Troubleshooting
Erreur "No module named scrapy"
bashpip install scrapy>=2.11
PDFs non téléchargés
Vérifier les permissions du dossier downloads/ et la config:
pythonFILES_STORE = './downloads'  # Chemin relatif
MEDIA_ALLOW_REDIRECTS = True
Rate limiting / 429 errors
Augmenter le délai:
bashscrapy crawl fribourg -s DOWNLOAD_DELAY=5 -s CONCURRENT_REQUESTS=1
🤝 Contribution

Fork le repository
Créer une branche: git checkout -b canton/nouveau-canton
Commiter: git commit -am 'Add nouveau canton spider'
Push: git push origin canton/nouveau-canton
Créer une Pull Request

📝 Scripts utiles
run_all.sh - Lancer tous les spiders
bash#!/bin/bash
# run_all.sh
SPIDERS=(fribourg vaud geneve)
DATE=$(date +%Y%m%d)

for spider in "${SPIDERS[@]}"; do
    echo "🕷️ Lancement de $spider..."
    scrapy crawl $spider -o "output/${spider}_${DATE}.jsonl" \
        -L INFO \
        -s HTTPCACHE_ENABLED=False
done

echo "✅ Tous les spiders terminés"
check_updates.py - Vérifier les changements
python#!/usr/bin/env python
# check_updates.py
import json
from pathlib import Path
from datetime import datetime, timedelta

def check_recent_decisions(jsonl_file, days=1):
    """Vérifie les nouvelles décisions des derniers jours"""
    cutoff = datetime.now() - timedelta(days=days)
    new_count = 0
    
    with open(jsonl_file) as f:
        for line in f:
            item = json.loads(line)
            pub_date = datetime.fromisoformat(item['publication_date'])
            if pub_date >= cutoff:
                new_count += 1
                print(f"Nouvelle décision: {item['docket_number']} - {item['title']}")
    
    print(f"\n📊 Total: {new_count} nouvelles décisions")
    return new_count

if __name__ == "__main__":
    for jsonl in Path("output").glob("*.jsonl"):
        print(f"\n📁 Analyse de {jsonl.name}")
        check_recent_decisions(jsonl)
deploy.sh - Déploiement production
bash#!/bin/bash
# deploy.sh
echo "🚀 Déploiement Tribuna Scraper"

# Build Docker image
docker build -t tribuna-scraper:latest .

# Run avec volume pour output
docker run -v $(pwd)/output:/app/output \
    -e SCRAPY_SETTINGS_MODULE=tribuna_scraper.settings \
    tribuna-scraper:latest \
    scrapy crawl fribourg

# Upload to S3 (optionnel)
aws s3 sync output/ s3://my-bucket/tribuna-decisions/
📄 License
MIT License - Voir LICENSE
🙋 Support

Issues: GitHub Issues
Email: fuldry@ausarl.ch


Note: Ce scraper respecte les robots.txt et les conditions d'utilisation des sites. Utilisez-le de manière responsable avec des délais appropriés entre les requêtes.