"""Spider State Management - Séparation état/config du spider.

Ce module résout le problème des 15+ attributs d'instance mélangés
dans fribourg_spider.py en séparant :
- Configuration immutable (SpiderConfig)
- État runtime modifiable (SpiderState)
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Set, Dict, Optional
from pathlib import Path
import json


@dataclass
class SpiderState:
    """État runtime du spider (modifiable pendant l'exécution).
    
    Regroupe tous les attributs qui changent pendant le scraping :
    - Compteurs (pages, items)
    - Sets de tracking (seen_ids, yielded_docids)
    - Flags temporaires (older_seen)
    
    Attributes:
        page_nr: Numéro de la prochaine page à requêter
        pages_processed: Nombre de pages traitées
        items_emitted: Nombre d'items yieldés
        seen_ids: DocIds déjà rencontrés (déduplication)
        yielded_docids: DocIds déjà émis comme items
        older_seen: Flag indiquant qu'on a atteint des items trop anciens
        trefferzahl: Nombre total de résultats (depuis GWT)
        bootstrap_retry_count: Compteur de tentatives bootstrap
        bootstrap_perm_found: Flag bootstrap permutation trouvée
    
    Example:
        >>> state = SpiderState()
        >>> state.can_emit(max_items=100)
        True
        >>> state.record_emit('abc123...')
        >>> state.items_emitted
        1
    """
    
    page_nr: int = 0
    pages_processed: int = 0
    items_emitted: int = 0
    seen_ids: Set[str] = field(default_factory=set)
    yielded_docids: Set[str] = field(default_factory=set)
    older_seen: bool = False
    trefferzahl: int = 0
    bootstrap_retry_count: int = 0
    bootstrap_perm_found: bool = False
    
    def can_emit(self, max_items: int) -> bool:
        """Vérifie si on peut encore émettre des items.
        
        Args:
            max_items: Limite maximale (0 = illimité)
            
        Returns:
            True si on peut émettre, False sinon
        """
        if max_items <= 0:
            return True
        return self.items_emitted < max_items
    
    def record_emit(self, doc_id: str) -> None:
        """Enregistre l'émission d'un item.
        
        Args:
            doc_id: DocId de l'item émis
        """
        if doc_id:
            self.yielded_docids.add(doc_id)
        self.items_emitted += 1
    
    def is_doc_seen(self, doc_id: str) -> bool:
        """Vérifie si un DocId a déjà été vu.
        
        Args:
            doc_id: DocId à vérifier
            
        Returns:
            True si déjà vu, False sinon
        """
        return doc_id in self.seen_ids
    
    def mark_seen(self, doc_id: str) -> None:
        """Marque un DocId comme vu.
        
        Args:
            doc_id: DocId à marquer
        """
        if doc_id:
            self.seen_ids.add(doc_id)
    
    def is_doc_yielded(self, doc_id: str) -> bool:
        """Vérifie si un DocId a déjà été yieldé.
        
        Args:
            doc_id: DocId à vérifier
            
        Returns:
            True si déjà yieldé, False sinon
        """
        return doc_id in self.yielded_docids
    
    def summary(self) -> Dict:
        """Génère un résumé de l'état actuel.
        
        Returns:
            Dict avec les statistiques principales
        """
        return {
            'pages_processed': self.pages_processed,
            'items_emitted': self.items_emitted,
            'docids_seen': len(self.seen_ids),
            'docids_yielded': len(self.yielded_docids),
            'trefferzahl': self.trefferzahl,
            'older_seen': self.older_seen,
        }


@dataclass
class SpiderConfig:
    """Configuration immutable du spider.
    
    Regroupe toutes les valeurs de configuration qui ne changent pas
    pendant l'exécution :
    - Config canton (URLs, headers, templates)
    - Limites (MAX_PAGES, MAX_ITEMS)
    - Dates (min_date, max_date)
    - Cutoffs futurs
    - Features flags (barrier, etc.)
    
    Attributes:
        canton: Nom du canton
        kanton_kurz: Code canton (FR, GR, etc.)
        days: Nombre de jours en mode incrémental (None = full run)
        min_date: Date minimum pour filtre incrémental
        max_date: Date maximum (généralement aujourd'hui)
        pdatum_future_days: Tolérance jours futurs pour PDatum
        edatum_future_days: Tolérance jours futurs pour EDatum
        MAX_PAGES: Limite de pages à scraper (0 = illimité)
        MAX_ITEMS: Limite d'items à émettre (0 = illimité)
        PAGE_PDF_BARRIER: Activer la barrière pagination/PDF
        result_page_url: URL endpoint GWT loadTable
        decrypt_page_url: URL endpoint GWT decrypt
        download_url: URL base pour téléchargement PDF
        headers: Headers HTTP pour requêtes
        result_query_tpl: Template body GWT (full run)
        result_query_sort: Template body GWT (sort par date publication)
        decrypt_start: Début template decrypt
        decrypt_end: Fin template decrypt
        encrypted: PDF chiffrés (besoin decrypt)
        ascii_encrypted: Ancien format ASCII
    
    Example:
        >>> config = SpiderConfig.from_config_dict(raw_config, days=30, settings)
        >>> config.is_incremental()
        True
        >>> config.min_date
        date(2026, 1, 6)
    """
    
    canton: str
    kanton_kurz: str
    days: Optional[int]
    min_date: Optional[date]
    max_date: Optional[date]
    pdatum_future_days: int
    edatum_future_days: int
    MAX_PAGES: int
    MAX_ITEMS: int
    PAGE_PDF_BARRIER: bool
    
    # URLs et endpoints
    result_page_url: str
    decrypt_page_url: str
    download_url: str
    
    # Headers et templates
    headers: Dict[str, str]
    result_query_tpl: str
    result_query_sort: str
    decrypt_start: str
    decrypt_end: str
    
    # PDF config
    encrypted: bool
    ascii_encrypted: bool
    
    # Config raw (pour accès custom)
    _raw_config: Dict = field(default_factory=dict, repr=False)
    
    def is_incremental(self) -> bool:
        """Vérifie si on est en mode incrémental (days=N).
        
        Returns:
            True si mode incrémental, False si full run
        """
        return self.days is not None and self.days > 0
    
    def get_future_cutoff_pd(self) -> Optional[date]:
        """Calcule la date cutoff pour PDatum.
        
        Returns:
            Date cutoff (today + pdatum_future_days) ou max_date si plus petit
        """
        today = datetime.now(timezone.utc).date()
        cutoff = today + timedelta(days=self.pdatum_future_days)
        
        if self.max_date and self.max_date < cutoff:
            return self.max_date
        
        return cutoff
    
    def get_future_cutoff_ed(self) -> Optional[date]:
        """Calcule la date cutoff pour EDatum.
        
        Returns:
            Date cutoff (today + edatum_future_days)
        """
        today = datetime.now(timezone.utc).date()
        return today + timedelta(days=self.edatum_future_days)
    
    @classmethod
    def from_config_dict(
        cls,
        config: Dict,
        days: Optional[int],
        settings,
    ) -> 'SpiderConfig':
        """Factory depuis cantons_config.json.
        
        Args:
            config: Dict de config du canton
            days: Argument spider --days
            settings: Scrapy settings
            
        Returns:
            SpiderConfig initialisé
        """
        # Calculer min/max dates si mode incrémental
        min_date = None
        max_date = None
        today = datetime.now(timezone.utc).date()
        
        if days and days > 0:
            min_date = today - timedelta(days=days)
            max_date = today
        
        # Extraire les valeurs
        # Settings peut être None si appelé depuis __init__ (pas encore disponible)
        max_pages_setting = 0
        max_items_setting = 0
        if settings:
            max_pages_setting = settings.getint('MAX_PAGES', 0)
            max_items_setting = settings.getint('MAX_ITEMS', 0)
        
        return cls(
            canton=config.get('name', 'unknown'),
            kanton_kurz=config.get('kanton_kurz', 'XX'),
            days=days,
            min_date=min_date,
            max_date=max_date,
            pdatum_future_days=int(config.get('pdatum_future_days', 0)),
            edatum_future_days=int(config.get('edatum_future_days', 14)),
            MAX_PAGES=max_pages_setting or int(config.get('max_pages', 0)),
            MAX_ITEMS=max_items_setting,
            PAGE_PDF_BARRIER=config.get('page_pdf_barrier', True),
            result_page_url=config['result_page_url'],
            decrypt_page_url=config['decrypt_page_url'],
            download_url=config.get('download_url', config.get('base_url', '')),
            headers=dict(config['headers']),
            result_query_tpl=config['result_query_tpl'],
            result_query_sort=config.get('ui_publication_sort_body', config['result_query_tpl']),
            decrypt_start=config.get('decrypt_start', ''),
            decrypt_end=config.get('decrypt_end', ''),
            encrypted=config.get('encrypted', True),
            ascii_encrypted=config.get('ascii_encrypted', False),
            _raw_config=config,
        )
