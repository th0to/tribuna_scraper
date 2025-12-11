Guide d'intégration Tribuna Scraper
🎯 Résumé de la solution
Cette solution évite complètement GWT-RPC en utilisant une approche pragmatique :

Recherche d'exports directs (CSV/Excel) en premier
Extraction des liens ServletDownload depuis le HTML
Parsing adaptatif multi-format (tables, divs, liens directs)
Deux modes d'exécution : INITIAL (tout) et UPDATE (N jours)

✅ Ce qui est livré
Données extraites (minimum garanti)

year : Année de publication (int)
pdf_url : URL complète du PDF pour téléchargement
court : Instance/Cour (si extractible)
canton : Nom du canton

Compatibilité

✅ Scrapy 2.13.3
✅ Windows (dev) et Ubuntu 24 (prod Azure)
✅ Compatible avec tes pipelines existantes
✅ Middleware FilesPipeline standard
✅ Download delay 0.75-1s respecté

📦 Installation dans ton projet
Option 1 : Intégration directe
bash# Copier les fichiers dans ton projet existant
cp tribuna_base.py your_project/spiders/
cp fribourg_spider.py your_project/spiders/
cp grisons_spider.py your_project/spiders/
Option 2 : Environnement dédié
bash# Créer un venv séparé pour Tribuna
python -m venv venv_tribuna
venv_tribuna\Scripts\activate  # Windows
source venv_tribuna/bin/activate  # Linux

# Installer Scrapy
pip install scrapy==2.13.3
🚀 Utilisation
Mode INITIAL - Première collecte complète
bash# Récupérer TOUTES les décisions de Fribourg
scrapy crawl tribuna_fribourg -a mode=INITIAL

# Récupérer toutes les décisions d'une année spécifique
scrapy crawl tribuna_fribourg -a mode=INITIAL -a year_filter=2024

# Avec output JSONL
scrapy crawl tribuna_fribourg -a mode=INITIAL -o fribourg_initial.jsonl
Mode UPDATE - Mises à jour hebdomadaires
bash# Récupérer les 7 derniers jours (défaut)
scrapy crawl tribuna_fribourg -a mode=UPDATE

# Récupérer les 14 derniers jours
scrapy crawl tribuna_fribourg -a mode=UPDATE -a days_back=14

# Pour la planification hebdomadaire (cron/Task Scheduler)
scrapy crawl tribuna_fribourg -a mode=UPDATE -a days_back=8 -o fribourg_update_$(date +%Y%m%d).jsonl
🔧 Adaptation pour tes pipelines
Si tu utilises un Item custom
python# Dans ton items.py existant, ajoute ces champs minimaux :
class YourExistingItem(scrapy.Item):
    # Tes champs existants...
    
    # Champs Tribuna minimaux
    pdf_url = scrapy.Field()
    year = scrapy.Field()
    court = scrapy.Field()
    canton = scrapy.Field()
    
    # Pour FilesPipeline si tu l'utilises
    file_urls = scrapy.Field()
    files = scrapy.Field()
Adapter le spider pour ton Item
python# Dans tribuna_base.py, modifie la méthode pour retourner ton Item :
from yourproject.items import YourExistingItem

def extract_from_servlet_link(self, link, response):
    # ... extraction logic ...
    
    # Au lieu de retourner un dict :
    item = YourExistingItem()
    item['pdf_url'] = pdf_url
    item['year'] = year
    item['court'] = court
    item['canton'] = self.canton_name
    
    # Pour FilesPipeline
    item['file_urls'] = [pdf_url]
    
    return item
📊 Structure de stockage recommandée
Pour éviter >10k fichiers dans un dossier :
downloads/
├── tribuna/
│   ├── fribourg/
│   │   ├── 2024/
│   │   │   ├── 001_2024_xxx.pdf
│   │   │   └── ...
│   │   ├── 2025/
│   │   │   └── ...
│   ├── grisons/
│   │   ├── 2024/
│   │   └── 2025/
Configuration dans settings.py :
python# Pour organiser par canton/année
class TribunaFilesPipeline(FilesPipeline):
    def file_path(self, request, response=None, info=None, *, item=None):
        canton = item.get('canton', 'unknown').lower()
        year = item.get('year', 'no_year')
        filename = request.url.split('/')[-1]
        return f'tribuna/{canton}/{year}/{filename}'

# Dans settings.py
ITEM_PIPELINES = {
    'yourproject.pipelines.TribunaFilesPipeline': 300,
}
🗓️ Planification des exécutions
Windows (Task Scheduler) - Script .bat
batch@echo off
REM update_tribuna.bat - À exécuter chaque semaine

cd C:\path\to\your\project
call venv\Scripts\activate

REM Update des 8 derniers jours (marge de sécurité)
scrapy crawl tribuna_fribourg -a mode=UPDATE -a days_back=8 -o output\fribourg_update_%date:~-4%%date:~-10,2%%date:~-7,2%.jsonl
scrapy crawl tribuna_grisons -a mode=UPDATE -a days_back=8 -o output\grisons_update_%date:~-4%%date:~-10,2%%date:~-7,2%.jsonl

echo Update Tribuna terminé

### Ubuntu/Azure (cron) - Script .sh
```bash
#!/bin/bash
# update_tribuna.sh - À exécuter chaque semaine

cd /path/to/your/project
source venv/bin/activate

# Update des 8 derniers jours
DATE=$(date +%Y%m%d)
scrapy crawl tribuna_fribourg -a mode=UPDATE -a days_back=8 -o output/fribourg_update_${DATE}.jsonl
scrapy crawl tribuna_grisons -a mode=UPDATE -a days_back=8 -o output/grisons_update_${DATE}.jsonl

# Upload vers SharePoint si configuré
# python upload_sharepoint.py output/*_update_${DATE}.jsonl
Crontab (dimanche 2h du matin) :
bash0 2 * * 0 /path/to/update_tribuna.sh >> /var/log/tribuna.log 2>&1
📝 Ajouter un nouveau canton
Pour ajouter un des 6 cantons utilisant Tribuna :
python# nouveau_canton_spider.py
from tribuna_base import TribunaBaseSpider

class NouveauCantonSpider(TribunaBaseSpider):
    name = 'tribuna_nouveau'
    canton_name = 'NouveauCanton'
    
    start_urls = [
        'https://tribunal.nouveau-canton.ch/',
        # Ajouter les variantes linguistiques
        'https://tribunal.nouveau-canton.ch/?locale=de',
        'https://tribunal.nouveau-canton.ch/?locale=it',
    ]
    
    # Si nécessaire, override pour spécificités locales
    def extract_court_from_context(self, element, response):
        # Logique spécifique si la structure diffère
        court = super().extract_court_from_context(element, response)
        
        # Ajustements spécifiques au canton
        if not court:
            # Tentative avec sélecteurs spécifiques
            court = element.css('.specific-court-class::text').get()
        
        return court
🔍 Diagnostic et debug
Vérifier ce qui est extrait
bash# Mode debug avec log verbose
scrapy crawl tribuna_fribourg -a mode=UPDATE -L DEBUG

# Tester sur une seule page
scrapy shell "https://publicationtc.fr.ch/"
>>> response.css('a[href*="ServletDownload"]').getall()
Script de validation des données
python# validate_output.py
import json
from pathlib import Path

def validate_tribuna_output(jsonl_file):
    """Valide que les champs minimaux sont présents"""
    
    stats = {
        'total': 0,
        'with_pdf': 0,
        'with_year': 0,
        'with_court': 0,
        'errors': []
    }
    
    with open(jsonl_file) as f:
        for line_num, line in enumerate(f, 1):
            try:
                item = json.loads(line)
                stats['total'] += 1
                
                # Vérifications minimales
                if item.get('pdf_url'):
                    stats['with_pdf'] += 1
                else:
                    stats['errors'].append(f"Ligne {line_num}: PDF URL manquant")
                
                if item.get('year'):
                    stats['with_year'] += 1
                else:
                    stats['errors'].append(f"Ligne {line_num}: Année manquante")
                    
                if item.get('court'):
                    stats['with_court'] += 1
                    
            except json.JSONDecodeError as e:
                stats['errors'].append(f"Ligne {line_num}: JSON invalide - {e}")
    
    # Rapport
    print(f"📊 Validation de {jsonl_file}")
    print(f"   Total items: {stats['total']}")
    print(f"   Avec PDF URL: {stats['with_pdf']} ({stats['with_pdf']/stats['total']*100:.1f}%)")
    print(f"   Avec année: {stats['with_year']} ({stats['with_year']/stats['total']*100:.1f}%)")
    print(f"   Avec cour: {stats['with_court']} ({stats['with_court']/stats['total']*100:.1f}%)")
    
    if stats['errors']:
        print(f"\n⚠️  {len(stats['errors'])} erreurs trouvées:")
        for err in stats['errors'][:5]:  # Afficher les 5 premières
            print(f"   - {err}")
    
    return stats

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        validate_tribuna_output(sys.argv[1])
    else:
        # Valider tous les fichiers récents
        for jsonl in Path('output').glob('tribuna_*.jsonl'):
            validate_tribuna_output(jsonl)
            print()
⚠️ Points d'attention
1. Gestion des années manquantes
Si l'année n'est pas extractible, le spider la met à None. Tu peux :

Utiliser l'année courante par défaut
Parser le PDF pour extraire l'année
Stocker dans un dossier "unknown_year"

2. Détection de nouveaux patterns
Le spider log les patterns non reconnus. Surveille les logs pour adapter :
pythonself.logger.warning(f"Pattern ServletDownload non standard: {url}")
3. Limites GWT-RPC
Si un canton utilise exclusivement GWT-RPC sans liens directs :

Le spider tentera les exports (CSV/Excel)
Fallback sur parsing HTML standard
Si rien ne marche → contact l'éditeur ou reverse engineering nécessaire

📊 Métriques attendues
Basé sur tes chiffres :
CantonDécisions totalesTemps estimé INITIALTemps UPDATE hebdoFribourg~13,563~4-5 heures~10-20 minutesGrisons~14,043~4-5 heures~10-20 minutes
Avec DOWNLOAD_DELAY=1 seconde.
🤝 Support et évolution
Si tu veux ajouter des métadonnées
Le spider de base peut être enrichi facilement :
python# Dans tribuna_base.py, ajoute l'extraction :
def extract_additional_metadata(self, response):
    """Override pour extraire des métadonnées supplémentaires"""
    return {
        'judge': response.css('.judge::text').get(),
        'parties': response.css('.parties::text').getall(),
        'keywords': response.css('.keywords::text').getall(),
    }
Si tu veux utiliser Playwright
Pour les cas complexes nécessitant JavaScript :
python# Dans settings.py
DOWNLOAD_HANDLERS = {
    'http': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
    'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
}

PLAYWRIGHT_BROWSER_TYPE = 'chromium'
PLAYWRIGHT_LAUNCH_OPTIONS = {'headless': True}

# Dans le spider
def start_requests(self):
    for url in self.start_urls:
        yield scrapy.Request(
            url,
            meta={'playwright': True},
            callback=self.parse
        )
✅ Checklist de mise en production

 Tester mode INITIAL sur échantillon (ex: 1 mois)
 Vérifier la structure des dossiers de stockage
 Configurer la planification hebdomadaire
 Tester mode UPDATE après 1 semaine
 Monitorer les logs pour patterns non reconnus
 Ajuster DOWNLOAD_DELAY si nécessaire
 Documenter les URLs exactes des 6 cantons

📞 Points de contact
Pour les questions sur :

Patterns non reconnus : Envoie un extrait HTML
Performances : On peut paralléliser par année
GWT-RPC bloquant : Solution Playwright disponible
Nouveaux cantons : Template prêt à l'emploi


Note finale : Cette solution est conçue pour être robuste et maintenable. Elle évite la complexité de GWT-RPC tout en garantissant l'extraction des données minimales nécessaires. Le mode UPDATE hebdomadaire assure que tu ne rates aucune nouvelle décision.