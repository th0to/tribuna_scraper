"""Page Barrier - Gestion de la barrière de pagination.

Contrôle le rythme de pagination pour éviter d'avoir trop de requêtes
PDF concurrentes. La page N+1 n'est demandée que lorsque tous les
DocIds de la page N ont atteint un état terminal (PDF trouvé ou échec).
"""

import logging
from typing import Dict, Optional, Set

import scrapy

logger = logging.getLogger(__name__)


class PageBarrier:
    """Barrière de pagination pour contrôler le flow des requêtes PDF.
    
    Problème résolu:
    Une page GWT retourne ~20 DocIds. Pour chacun, on peut déclencher
    0..1 requête decrypt, puis 1..k tentatives de téléchargement PDF.
    Sans contrôle, on multiplie les requêtes concurrentes.
    
    Solution:
    On bloque l'émission de la requête "page suivante" tant que tous
    les DocIds de la page courante n'ont pas terminé.
    """
    
    def __init__(self, enabled: bool = True):
        """
        Args:
            enabled: Activer/désactiver la barrière
        """
        self.enabled = enabled
        self._pending_docids: Dict[int, Set[str]] = {}  # page_nr -> set(docid)
        self._next_requests: Dict[int, scrapy.Request] = {}  # page_nr -> Request
        self._emitted_pages: Set[int] = set()  # pages dont next a été émis
        
    def register(self, page_nr: int, docid: Optional[str]) -> None:
        """Enregistre un DocId comme "pending" pour sa page.
        
        Appelé juste avant de yield une Request (decrypt/PDF).
        
        Args:
            page_nr: Numéro de page
            docid: DocId à enregistrer
        """
        if not self.enabled:
            return
        if page_nr is None or not docid:
            return
            
        if page_nr not in self._pending_docids:
            self._pending_docids[page_nr] = set()
        self._pending_docids[page_nr].add(docid)
        
    def done(self, page_nr: int, docid: Optional[str]) -> Optional[scrapy.Request]:
        """Marque un DocId comme terminé et débloque si nécessaire.
        
        Appelé quand un DocId atteint l'état terminal (PDF OK ou échec).
        
        Args:
            page_nr: Numéro de page
            docid: DocId terminé
            
        Returns:
            Request de la page suivante si tous les DocIds sont terminés
            et qu'une requête était en attente, sinon None
        """
        if not self.enabled:
            return None
        if page_nr is None or not docid:
            return None
            
        # Retirer le DocId des pending
        pending = self._pending_docids.get(page_nr)
        if pending is not None:
            pending.discard(docid)
            
            # S'il reste des DocIds pending, rester bloqué
            if len(pending) > 0:
                return None
                
            # Nettoyer
            self._pending_docids.pop(page_nr, None)
            
        # Vérifier si on a une requête en attente
        if page_nr in self._emitted_pages:
            return None
            
        next_req = self._next_requests.pop(page_nr, None)
        if next_req is not None:
            self._emitted_pages.add(page_nr)
            logger.debug(f"Barrier: page {page_nr} terminée, émission page suivante")
            return next_req
            
        return None
    
    def store_next_request(self, page_nr: int, request: scrapy.Request) -> bool:
        """Stocke la requête de la page suivante si barrière active.
        
        Args:
            page_nr: Numéro de page courante
            request: Request pour la page suivante
            
        Returns:
            True si la requête a été stockée (barrière active),
            False si elle doit être émise immédiatement
        """
        if not self.enabled:
            return False
            
        pending = self._pending_docids.get(page_nr)
        if pending and len(pending) > 0:
            self._next_requests[page_nr] = request
            logger.info(
                f"Barrier: attente page {page_nr} (pending={len(pending)}), "
                "pagination différée"
            )
            return True
            
        return False
    
    def get_pending_count(self, page_nr: int) -> int:
        """Retourne le nombre de DocIds pending pour une page."""
        pending = self._pending_docids.get(page_nr)
        return len(pending) if pending else 0
    
    def is_page_complete(self, page_nr: int) -> bool:
        """Vérifie si une page a tous ses DocIds terminés."""
        return self.get_pending_count(page_nr) == 0
    
    def reset(self) -> None:
        """Réinitialise la barrière (utile pour tests)."""
        self._pending_docids.clear()
        self._next_requests.clear()
        self._emitted_pages.clear()
