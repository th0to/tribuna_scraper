"""GWT Token Cache - Cache local pour tokens GWT-RPC.

Ce module évite le bootstrap à chaque run en cachant les tokens
X-GWT-Permutation et X-GWT-Module-Base avec un TTL de 24h.

Performance gain : -2 à -3 secondes par run (skip 2-3 requêtes bootstrap)
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class GWTTokenCache:
    """Cache local pour tokens GWT avec TTL.
    
    Les tokens GWT (X-GWT-Permutation, X-GWT-Module-Base) sont générés
    lors du déploiement de l'application et restent valides jusqu'au
    prochain déploiement (généralement plusieurs jours/semaines).
    
    Ce cache évite de refaire le bootstrap (2-3 requêtes) à chaque run
    en sauvegardant les tokens dans un fichier JSON local.
    
    Attributes:
        cache_dir: Répertoire de cache
        ttl: Durée de validité du cache (default: 24h)
    
    Example:
        >>> cache = GWTTokenCache(Path.home() / '.tribuna_cache')
        >>> 
        >>> # Essayer de charger depuis cache
        >>> tokens = cache.get('fribourg')
        >>> if tokens:
        >>>     headers.update(tokens)
        >>> else:
        >>>     # Faire bootstrap
        >>>     perm, module = do_bootstrap()
        >>>     cache.set('fribourg', perm, module)
    """
    
    def __init__(self, cache_dir: Path, ttl_hours: int = 24):
        """Initialize cache.
        
        Args:
            cache_dir: Répertoire où stocker les fichiers cache
            ttl_hours: Durée de validité en heures (default: 24)
        """
        self.cache_dir = Path(cache_dir)
        self.ttl = timedelta(hours=ttl_hours)
        
        # Créer le répertoire si nécessaire
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Cannot create cache dir {self.cache_dir}: {e}")
    
    def get(self, canton: str) -> Optional[Dict[str, str]]:
        """Récupère les tokens cachés si valides.
        
        Args:
            canton: Nom du canton (ex: 'fribourg')
            
        Returns:
            Dict {'X-GWT-Permutation': '...', 'X-GWT-Module-Base': '...'}
            ou None si cache inexistant/expiré
            
        Example:
            >>> tokens = cache.get('fribourg')
            >>> if tokens:
            >>>     print(f"Permutation: {tokens['X-GWT-Permutation']}")
        """
        cache_file = self._get_cache_file(canton)
        
        # Vérifier existence
        if not cache_file.exists():
            logger.debug(f"Cache miss: {cache_file} not found")
            return None
        
        # Charger et vérifier TTL
        try:
            data = json.loads(cache_file.read_text(encoding='utf-8'))
            
            # Parser timestamp
            cached_at = datetime.fromisoformat(data['timestamp'])
            age = datetime.now() - cached_at
            
            # Vérifier expiration
            if age > self.ttl:
                logger.info(f"Cache expired: {cache_file} (age: {age})")
                return None
            
            # Vérifier présence des clés
            if 'permutation' not in data or 'module_base' not in data:
                logger.warning(f"Cache incomplete: {cache_file}")
                return None
            
            logger.info(f"Cache hit: {canton} (age: {age}, TTL: {self.ttl})")
            
            return {
                'X-GWT-Permutation': data['permutation'],
                'X-GWT-Module-Base': data['module_base'],
            }
        
        except Exception as e:
            logger.warning(f"Cache read error: {cache_file}: {e}")
            return None
    
    def set(self, canton: str, permutation: str, module_base: str) -> bool:
        """Sauvegarde les tokens dans le cache.
        
        Args:
            canton: Nom du canton
            permutation: X-GWT-Permutation value
            module_base: X-GWT-Module-Base value
            
        Returns:
            True si sauvegarde réussie, False sinon
            
        Example:
            >>> cache.set('fribourg', 'BE9F9A080DBAD6CB...', 'https://...')
            True
        """
        cache_file = self._get_cache_file(canton)
        
        data = {
            'timestamp': datetime.now().isoformat(),
            'canton': canton,
            'permutation': permutation,
            'module_base': module_base,
        }
        
        try:
            cache_file.write_text(json.dumps(data, indent=2), encoding='utf-8')
            logger.info(f"Cache saved: {cache_file}")
            return True
        except Exception as e:
            logger.error(f"Cache write error: {cache_file}: {e}")
            return False
    
    def invalidate(self, canton: str) -> bool:
        """Invalide le cache pour un canton.
        
        Args:
            canton: Nom du canton
            
        Returns:
            True si suppression réussie, False sinon
        """
        cache_file = self._get_cache_file(canton)
        
        try:
            if cache_file.exists():
                cache_file.unlink()
                logger.info(f"Cache invalidated: {cache_file}")
                return True
            return False
        except Exception as e:
            logger.error(f"Cache invalidation error: {cache_file}: {e}")
            return False
    
    def _get_cache_file(self, canton: str) -> Path:
        """Génère le chemin du fichier cache.
        
        Args:
            canton: Nom du canton
            
        Returns:
            Path du fichier cache
        """
        safe_name = canton.lower().replace(' ', '_')
        return self.cache_dir / f"{safe_name}_gwt_tokens.json"
