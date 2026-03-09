"""PDF Decryptor - Gestion du décryptage et vérification des PDFs.

Pour Fribourg, les chemins PDF sont chiffrés et nécessitent une requête
de décryptage avant de pouvoir télécharger le fichier.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from publication_scraper.spiders import gwt_utils

logger = logging.getLogger(__name__)


class PDFDecryptor:
    """Gestion du décryptage des chemins PDF pour Fribourg.
    
    Workflow:
    1. Extraire le pfad (chemin chiffré) depuis les tokens GWT
    2. Envoyer une requête POST avec le pfad pour obtenir le token déchiffré
    3. Construire les URLs candidates pour le PDF
    4. Vérifier chaque candidate jusqu'à trouver un PDF valide
    """
    
    # Patterns pour détecter les chemins PDF
    RE_PFAD = gwt_utils.RE_PFAD
    RE_PFAD2 = gwt_utils.RE_PFAD2
    
    def __init__(self, config: Dict):
        """
        Args:
            config: Configuration Fribourg (depuis cantons_config.json)
        """
        self.config = config
        self.decrypt_start = config['decrypt_start']
        self.decrypt_end = config['decrypt_end']
        self.download_url = config['download_url']
        self.pdf_path = config['pdf_path']
        self.pdf_pattern = config['pdf_pattern']
        
    def detect_pdf_path(
        self,
        tokens: List[str],
        doc_id: Optional[str] = None,
        page_content: Optional[str] = None,
        doc_id_pos: Optional[int] = None,
    ) -> Tuple[Optional[str], bool]:
        """Détecte le chemin PDF (pfad) dans les tokens.
        
        Args:
            tokens: Liste de tokens de la ligne GWT
            doc_id: DocId pour recherche fallback
            page_content: Contenu brut de la page pour fallback
            doc_id_pos: Position du DocId dans le contenu
            
        Returns:
            Tuple (pfad, neuePfadsyntax) - pfad est None si non trouvé
        """
        neuePfadsyntax = False
        pfad: Optional[str] = None
        
        # Recherche dans les tokens
        for tok in tokens:
            tok = tok or ''
            if self.RE_PFAD.fullmatch(tok):
                pfad = tok
                break
            elif self.RE_PFAD2.fullmatch(tok):
                pfad = tok
                neuePfadsyntax = True
                break
                
        # Fallback: recherche dans le contenu brut
        if not pfad and doc_id and page_content:
            fallback, fallback_neue = self._search_in_content(doc_id, page_content, doc_id_pos)
            if fallback:
                pfad = fallback
                neuePfadsyntax = fallback_neue
                logger.debug(f"Pfad fallback pour DocID {doc_id}: {pfad[:32]}...")
                
        return pfad, neuePfadsyntax
    
    def _search_in_content(
        self, 
        doc_id: str, 
        content: str, 
        doc_id_pos: Optional[int] = None
    ) -> Tuple[Optional[str], bool]:
        """Recherche un chemin PDF dans le contenu autour d'un DocId."""
        if not doc_id or not content:
            return None, False
            
        occurrences: List[int] = []
        seen = set()
        
        if doc_id_pos is not None:
            occurrences.append(doc_id_pos)
            seen.add(doc_id_pos)
            
        # Trouver toutes les occurrences du DocId
        search_start = 0
        while True:
            idx = content.find(doc_id, search_start)
            if idx == -1:
                break
            if idx not in seen:
                occurrences.append(idx)
                seen.add(idx)
            search_start = idx + len(doc_id)
            
        def find_in_windows(index: int) -> Tuple[Optional[str], bool]:
            # Chercher après le DocId
            window_after = content[index: min(len(content), index + 900)]
            for pattern, flag in ((self.RE_PFAD, False), (self.RE_PFAD2, True)):
                match = pattern.search(window_after)
                if match:
                    return match.group(0), flag
                    
            # Chercher avant le DocId
            window_before = content[max(0, index - 600): index]
            for pattern, flag in ((self.RE_PFAD, False), (self.RE_PFAD2, True)):
                match = pattern.search(window_before)
                if match:
                    return match.group(0), flag
                    
            return None, False
            
        for idx in occurrences:
            pfad, flag = find_in_windows(idx)
            if pfad:
                return pfad, flag
                
        return None, False
    
    def build_decrypt_body(self, pfad: str, num: str, neuePfadsyntax: bool) -> str:
        """Construit le body de la requête de décryptage.
        
        Args:
            pfad: Chemin chiffré du PDF
            num: Numéro de dossier (avec underscores)
            neuePfadsyntax: True si nouvelle syntaxe de chemin
            
        Returns:
            Body pour la requête POST de décryptage
        """
        if neuePfadsyntax:
            pfad_encrypt = f"{num}_{pfad}|dossiernummer|{num}"
        else:
            pfad_encrypt = pfad
            
        return self.decrypt_start + pfad_encrypt + self.decrypt_end
    
    def build_pdf_candidates(self, decrypt_response_text: str, item: Dict) -> Optional[Tuple[str, List[str]]]:
        """Construit les URLs candidates pour le PDF depuis la réponse decrypt.
        
        Args:
            decrypt_response_text: Texte de la réponse de décryptage
            item: Item contenant DocId, Num, etc.
            
        Returns:
            Tuple (première_url, [autres_candidates]) ou None si échec
        """
        try:
            numstr = str(item.get('Num', '')).replace(' ', '_')
            docid = str(item.get('DocId', ''))
        except Exception:
            return None
            
        # Extraire le token déchiffré
        decoded = self._extract_decoded_token(decrypt_response_text)
        if not decoded:
            return None
            
        # Construire les candidates
        candidates = self._generate_candidate_urls(decoded, numstr, docid)
        if not candidates:
            return None
            
        first = candidates.pop(0)
        return first, candidates
    
    def _extract_decoded_token(self, text: str) -> Optional[str]:
        """Extrait le token déchiffré de la réponse GWT."""
        # Pattern principal
        match = gwt_utils.RE_DECRYPT.search(text)
        if match:
            return match.group(1)
            
        # Pattern alternatif
        match2 = gwt_utils.RE_DECRYPT2.search(text)
        if match2:
            return match2.group(1)
            
        # Fallback: chercher un chemin dans la réponse
        match3 = gwt_utils.RE_DECODE.search(text)
        if match3:
            return match3.group(0)
            
        return None
    
    def _generate_candidate_urls(self, decoded: str, numstr: str, docid: str) -> List[str]:
        """Génère les URLs candidates pour le PDF."""
        candidates = []
        base = self.download_url.rstrip('/')
        
        # Candidate principale avec le pattern configuré
        try:
            primary = self.pdf_pattern.format(
                base,
                numstr,
                docid,
                self.pdf_path,
                docid,
                numstr
            )
            candidates.append(primary)
        except Exception:
            pass
            
        # Candidates alternatives basées sur le token décodé
        if decoded:
            # Direct avec le token
            candidates.append(f"{base}/{decoded}")
            
            # Avec pdf_path
            candidates.append(f"{base}/{self.pdf_path}/{decoded}")
            
            # Variantes avec .pdf
            if not decoded.lower().endswith('.pdf'):
                candidates.append(f"{base}/{decoded}.pdf")
                candidates.append(f"{base}/{self.pdf_path}/{decoded}.pdf")
                
        # Dédupliquer en préservant l'ordre
        seen = set()
        unique = []
        for url in candidates:
            if url not in seen:
                seen.add(url)
                unique.append(url)
                
        return unique
    
    @staticmethod
    def is_pdf_response(response) -> bool:
        """Vérifie si une réponse HTTP est un PDF valide.
        
        Args:
            response: Réponse Scrapy
            
        Returns:
            True si c'est un PDF valide
        """
        # Vérifier le status
        if response.status not in (200, 206):
            return False
            
        # Vérifier le Content-Type
        ct = None
        try:
            ct = response.headers.get('Content-Type') or response.headers.get(b'Content-Type')
            if isinstance(ct, bytes):
                ct = ct.decode('utf-8', errors='ignore')
        except Exception:
            ct = None
            
        if ct:
            ct_lower = ct.lower()
            if 'application/pdf' in ct_lower:
                return True
            if 'application/octet-stream' in ct_lower:
                # Vérifier les premiers bytes
                try:
                    magic = response.body[:5]
                    if magic == b'%PDF-':
                        return True
                except Exception:
                    pass
                    
        # Fallback: vérifier le magic number
        try:
            if response.body and response.body[:5] == b'%PDF-':
                return True
        except Exception:
            pass
            
        return False
    
    def get_verify_headers(self, base_headers: Dict[str, str]) -> Dict[str, str]:
        """Retourne les headers pour les requêtes de vérification PDF."""
        hdrs = dict(base_headers)
        hdrs['Accept'] = 'application/pdf,*/*'
        # Utiliser Range pour ne télécharger que le début
        hdrs['Range'] = 'bytes=0-4095'
        return hdrs
