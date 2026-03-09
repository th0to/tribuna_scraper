"""GWT Token Manager - Bootstrap et gestion des tokens GWT-RPC.

Gère l'extraction dynamique des tokens X-GWT-Permutation et X-GWT-Module-Base
depuis les pages HTML et fichiers nocache.js.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import scrapy
from scrapy.http import Response

from publication_scraper.spiders import gwt_utils

logger = logging.getLogger(__name__)


class GWTTokenManager:
    """Gestion des tokens GWT pour les requêtes RPC.
    
    Les applications GWT utilisent des tokens dynamiques qui changent
    lors du redéploiement. Cette classe gère:
    - L'extraction depuis le HTML de bootstrap
    - Le suivi des nocache.js pour obtenir le strongName
    - La mise à jour des headers de requête
    """
    
    def __init__(self, config: Dict, headers: Dict[str, str]):
        """
        Args:
            config: Configuration canton (depuis cantons_config.json)
            headers: Headers HTTP initiaux (seront modifiés in-place)
        """
        self.config = config
        self.headers = headers
        self._bootstrap_perm_found = False
        self._bootstrap_retry_count = 0
        self.max_bootstrap_retries = 2
        
    def extract_tokens_from_html(self, response: Response) -> List[str]:
        """Extrait les tokens GWT depuis une réponse HTML.
        
        Met à jour self.headers avec X-GWT-Permutation et X-GWT-Module-Base.
        
        Args:
            response: Réponse Scrapy de la page bootstrap
            
        Returns:
            Liste des URLs nocache.js à suivre si permutation non trouvée
        """
        try:
            html = response.text or ""
        except Exception:
            html = ""
            
        base_url = getattr(response, 'url', self.config.get('base_url', ''))
        
        found_perm, found_module, nocache_urls = gwt_utils.extract_bootstrap_tokens(
            html,
            base_url=base_url,
            response=response,
        )
        
        if found_perm:
            self.headers['X-GWT-Permutation'] = found_perm
            logger.info(f"X-GWT-Permutation dynamique: {found_perm}")
            self._bootstrap_perm_found = True
            self._bootstrap_retry_count = 0
            
        if found_module:
            self.headers['X-GWT-Module-Base'] = found_module
            logger.info(f"X-GWT-Module-Base dynamique: {found_module}")
            
        if not found_perm:
            if nocache_urls:
                logger.info("Permutation non trouvée dans HTML; tentative via nocache.js")
            else:
                logger.warning("Permutation GWT non détectée; utilisation valeurs statiques")
                
        return nocache_urls
    
    def extract_tokens_from_nocache(self, response: Response) -> bool:
        """Extrait le strongName depuis un fichier nocache.js.
        
        Args:
            response: Réponse du fichier .nocache.js
            
        Returns:
            True si permutation trouvée, False sinon
        """
        try:
            text_nc = response.text or ''
        except Exception:
            text_nc = ''
            
        found_perm = None
        
        # Pattern 1: strongName explicite
        strong = re.search(r'strongName\s*[:=]\s*[\"\']([A-Za-z0-9._-]{8,})[\"\']', text_nc)
        if strong:
            found_perm = strong.group(1)
            
        # Pattern 2: référence à un fichier .cache.js
        if not found_perm:
            cache_hits = re.findall(r'([A-Za-z0-9._/-]+)\.cache\.js', text_nc)
            if cache_hits:
                found_perm = cache_hits[0].split('/')[-1].split('.')[0]
                
        # Pattern 3: hash hexadécimal 32 chars (Tribuna-specific)
        if not found_perm:
            hex32 = re.findall(r'"([A-Fa-f0-9]{32})"', text_nc) or re.findall(r"'([A-Fa-f0-9]{32})'", text_nc)
            if hex32:
                found_perm = hex32[0]
                logger.debug(f"Permutation extraite depuis hex32 nocache: {found_perm}")
                
        if found_perm:
            self.headers['X-GWT-Permutation'] = found_perm
            logger.info(f"X-GWT-Permutation via nocache.js: {found_perm}")
            self._bootstrap_perm_found = True
            self._bootstrap_retry_count = 0
            return True
        
        # Module-base fallback: déduire depuis l'URL nocache
        if 'X-GWT-Module-Base' not in self.headers:
            try:
                self.headers['X-GWT-Module-Base'] = str(response.url).rsplit('/', 1)[0] + '/'
                logger.info(f"X-GWT-Module-Base via nocache.js: {self.headers['X-GWT-Module-Base']}")
            except Exception:
                pass
            
        logger.warning("Permutation GWT non détectée dans nocache.js")
        return False
    
    def needs_retry(self) -> bool:
        """Vérifie si un retry bootstrap est possible."""
        return self._bootstrap_retry_count < self.max_bootstrap_retries
    
    def increment_retry(self) -> int:
        """Incrémente le compteur de retry et retourne la nouvelle valeur."""
        self._bootstrap_retry_count += 1
        return self._bootstrap_retry_count
    
    def is_incompatible_rpc_error(self, response_text: str) -> bool:
        """Détecte une erreur IncompatibleRemoteServiceException.
        
        Cette erreur survient quand le strongName est obsolète
        (application redéployée).
        """
        preview = response_text[:500] if response_text else ''
        return 'IncompatibleRemoteServiceException' in preview or 'strongName' in preview
    
    def get_bootstrap_url(self) -> str:
        """Retourne l'URL de bootstrap à utiliser."""
        return self.config.get('bootstrap_url') or self.config.get('base_url', '')
