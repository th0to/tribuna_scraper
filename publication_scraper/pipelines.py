import json
import csv
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from itemadapter import ItemAdapter
from scrapy.pipelines.files import FilesPipeline
import logging


class PublicationPipeline:
    """
    Pipeline minimale pour sauvegarder les métadonnées des publications
    
    Le client a ses propres pipelines pour gérer les PDF et HTML.
    Cette pipeline se limite à sauvegarder les métadonnées extraites.
    
    IMPORTANT: Le client devra intégrer ses propres pipelines:
    - Pipeline PDF (download automatique, détection doublons, etc.)
    - Pipeline HTML (si nécessaire)
    
    Cette pipeline peut être désactivée si le client préfère utiliser
    uniquement ses pipelines existantes.
    """
    
    def open_spider(self, spider):
        """
        Appelé quand le spider démarre
        """
        # Récupérer le dossier de sortie depuis les settings Scrapy (pas d'env vars)
        settings = getattr(spider, 'settings', None)
        if settings is None and getattr(spider, 'crawler', None) is not None:
            settings = getattr(spider.crawler, 'settings', None)

        def _get(name: str, default):
            try:
                return settings.get(name, default) if settings is not None else default
            except Exception:
                return default

        # Option to silence Scrapy's own DEBUG item dumps (these print full item dicts).
        # Default: enabled. Can be disabled via -s SILENCE_SCRAPY_ITEM_DEBUG=False
        try:
            if _get('SILENCE_SCRAPY_ITEM_DEBUG', True):
                try:
                    logging.getLogger('scrapy').setLevel(logging.INFO)
                except Exception:
                    pass
        except Exception:
            pass

        output_dir = _get('OUTPUT_DIR', './output')
        output_subdir = _get('OUTPUT_SUBDIR', 'publications')
        base_dir = Path(output_dir) / output_subdir
        output_format = _get('OUTPUT_FORMAT', 'json')
        
        # Créer le dossier de sortie s'il n'existe pas
        base_dir.mkdir(parents=True, exist_ok=True)
        
        # Nom du canton dans le fichier de sortie
        canton = getattr(spider, 'canton', 'unknown')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        self.items = []
        self.output_format = output_format
        self.json_file = None
        self.csv_file = None
        self.csv_writer = None
        
        # Ouvrir les fichiers selon le format demandé
        if output_format in ['json', 'both']:
            json_path = base_dir / f'publications_{canton}_{timestamp}.json'
            self.json_file = open(json_path, 'w', encoding='utf-8')
            spider.logger.info(f"Fichier JSON : {json_path}")
        
        if output_format in ['csv', 'both']:
            csv_path = base_dir / f'publications_{canton}_{timestamp}.csv'
            self.csv_file = open(csv_path, 'w', encoding='utf-8', newline='')
            
            # Colonnes basées sur le modèle client
            fieldnames = [
                'Kanton', 'DocId', 'Num', 'Signatur', 'Gericht', 'Kammer',
                'VGericht', 'VKammer', 'EDatum', 'PDatum', 'Titel',
                'Leitsatz', 'Rechtsgebiet', 'PDFUrls', 'HTMLUrls', 'PageNr'
            ]
            
            self.csv_writer = csv.DictWriter(
                self.csv_file,
                fieldnames=fieldnames,
                extrasaction='ignore'  # Ignorer les champs supplémentaires
            )
            self.csv_writer.writeheader()
            spider.logger.info(f"Fichier CSV : {csv_path}")
    
    def close_spider(self, spider):
        """
        Appelé quand le spider se termine
        """
        def _parse_iso_date(s: str):
            if not s:
                return None
            try:
                return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
            except Exception:
                return None

        def _should_sort_by_pdatum() -> bool:
            # Goal: match UI/server ordering when crawling with publication sort.
            try:
                return str(getattr(spider, 'sort', '') or '').strip().lower() == 'publication'
            except Exception:
                return False

        if _should_sort_by_pdatum() and self.items:
            try:
                order = str(getattr(spider, 'publication_order', 'desc') or 'desc').strip().lower()
            except Exception:
                order = 'desc'

            def sort_key(it: dict):
                pd = it.get('PDatum') if isinstance(it, dict) else None
                dt = _parse_iso_date(pd)
                # None dates sort last.
                ord_val = dt.toordinal() if dt is not None else -1
                # Stable tie-breakers: prefer deterministic keys.
                return (
                    ord_val,
                    str(it.get('Num') or ''),
                    str(it.get('DocId') or ''),
                )

            reverse = order != 'asc'
            try:
                self.items.sort(key=sort_key, reverse=reverse)
            except Exception:
                # Never fail the run just because output sorting failed.
                pass

        # Écrire les items dans les fichiers
        if self.json_file:
            json.dump(self.items, self.json_file, ensure_ascii=False, indent=2)
            self.json_file.close()
        
        if self.csv_file:
            self.csv_file.close()
        
        spider.logger.info(f"Total d'items extraits : {len(self.items)}")
        
        # Statistiques par année
        if self.items:
            years = {}
            for item in self.items:
                year = item.get('PDatum', '')[:4] if item.get('PDatum') else 'unknown'
                if year == '0000' or not year.isdigit():
                    year = 'unknown'
                years[year] = years.get(year, 0) + 1
            
            spider.logger.info("Répartition par année de publication:")
            for year in sorted(years.keys()):
                spider.logger.info(f"  {year}: {years[year]} décisions")
    
    def process_item(self, item, spider):
        """
        Traiter chaque item
        
        NOTE: Le client peut remplacer cette méthode par ses propres
        pipelines pour gérer le download des PDF, HTML, etc.
        """
        # Ensure item is a mapping or Scrapy Item
        try:
            item_dict = dict(item)
        except Exception:
            # If item is not directly convertible, try to coerce common types
            if hasattr(item, 'to_dict'):
                item_dict = item.to_dict()
            else:
                # Fallback: wrap item into a dict under 'raw'
                item_dict = {'raw': str(item)}
        
        # Convertir les listes en strings pour CSV
        if self.output_format in ['csv', 'both']:
            csv_item = item_dict.copy()
            if 'PDFUrls' in csv_item and isinstance(csv_item['PDFUrls'], list):
                csv_item['PDFUrls'] = '|'.join(csv_item['PDFUrls'])
            if 'HTMLUrls' in csv_item and isinstance(csv_item['HTMLUrls'], list):
                csv_item['HTMLUrls'] = '|'.join(csv_item['HTMLUrls'])
            self.csv_writer.writerow(csv_item)
        
        # Ajouter à la liste pour JSON
        self.items.append(item_dict)
        
        return item


class PDFDownloadPipeline:
    """
    Pipeline de téléchargement PDF - PLACEHOLDER
    
    Le client a déjà une pipeline PDF avec:
    - Middleware Scrapy pour download
    - Détection des doublons
    - Détection des fichiers mis à jour
    
    Cette classe est un placeholder pour montrer où intégrer
    la pipeline existante du client.
    
    À DÉSACTIVER si le client utilise ses propres pipelines.
    """
    
    def process_item(self, item, spider):
        """
        Le client peut implémenter ici sa logique de download PDF
        ou utiliser sa pipeline existante
        """
        # TODO: Intégrer la pipeline PDF du client
        # Exemple de ce qui pourrait être fait:
        # - Télécharger le PDF depuis item['PDFUrls']
        # - Stocker dans Sharepoint
        # - Organiser par année: item['PDatum'][:4]
        # - Organiser par cour: item['Gericht'] ou item['Kammer']
        
        return item


class HTMLDownloadPipeline:
    """
    Pipeline de téléchargement HTML - PLACEHOLDER
    
    Le client a une pipeline séparée pour HTML.
    Cette classe est un placeholder.
    
    À DÉSACTIVER si le client utilise ses propres pipelines.
    """
    
    def process_item(self, item, spider):
        """
        Le client peut implémenter ici sa logique de download HTML
        ou utiliser sa pipeline existante
        """
        # TODO: Intégrer la pipeline HTML du client
        
        return item


class PDFFilesPipeline(FilesPipeline):
    """FilesPipeline personnalisée pour nommer les PDF avec extension explicite."""

    def file_path(self, request, response=None, info=None, *, item=None):
        adapter = ItemAdapter(item) if item is not None else None

        # Choisir un identifiant de base: DocId > Num > nom de fichier URL
        docid = adapter.get('DocId') if adapter else None
        num = adapter.get('Num') if adapter else None

        parsed = urlparse(request.url)
        url_name = Path(parsed.path).name or 'document'
        name_root, name_ext = Path(url_name).stem, Path(url_name).suffix
        if not name_ext:
            name_ext = '.pdf'

        base = docid or num or name_root or 'document'
        # Sanitize base: keep alnum, dash, underscore
        base = re.sub(r'[^A-Za-z0-9._-]+', '_', str(base))

        # Organiser par canton si présent
        canton = adapter.get('Kanton') if adapter else None
        if canton:
            return f"full/{canton}/{base}{name_ext}"
        return f"full/{base}{name_ext}"
