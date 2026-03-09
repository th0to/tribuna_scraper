"""GWT Validator - Validation des données GWT-RPC.

Ce module valide les données extraites de GWT pour détecter les erreurs
de parsing (offsets incorrects, contamination cross-field, etc.)

Cas réels détectés dans RAPPORT_VALIDATION_NUM_DOCID.md :
- DocId 250928b8... avait Num = "java.util.HashMap/1797211028" ← Offset incorrect!
- DocId 043bc0a2... avait Num = "Assistance judiciaire..." ← Leitsatz au lieu de Num!
- DocId b587303d... avait Num = "Assurance-accidents..." ← Rechtsgebiet au lieu de Num!
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns de validation
RE_DOCID = re.compile(r'^[0-9a-f]{32}$', re.I)  # DocId: 32 hex chars
RE_DOSSIER_GENERIC = re.compile(r'^\d{1,4}\s+\d{4}\s+\d{1,6}$')  # Format: XXX YYYY ZZZ
RE_DOSSIER_UNDERSCORE = re.compile(r'^\d{1,4}_\d{4}_\d{1,6}$')  # Format: XXX_YYYY_ZZZ
RE_ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # Date ISO
RE_PFAD_WINDOWS = re.compile(r'^[A-Z]:\\[^"]+\.pdf$', re.I)  # Chemin Windows
RE_PFAD_HEX = re.compile(r'^[0-9a-f]{64,}$', re.I)  # Hash long (nouvelle syntaxe)

# Patterns de "contamination" (valeurs qui ne devraient PAS être un Num)
RE_JAVA_CLASS = re.compile(r'java\.(util|lang)', re.I)  # java.util.HashMap/...
RE_LONG_TEXT = re.compile(r'^.{50,}$')  # Texte trop long (probablement Leitsatz/Rechtsgebiet)


class GWTValidator:
    """Validateur pour données GWT-RPC.
    
    Cette classe détecte les erreurs de parsing GWT courantes :
    - Format invalide (DocId pas 32 hex, Num pas XXX YYYY ZZZ)
    - Contamination cross-field (Leitsatz dans Num, HashMap dans Num)
    - Valeurs suspectes (texte trop long, patterns Java, etc.)
    
    Example:
        >>> GWTValidator.validate_docid("e1b042e30d1c43579449bcce8668b493")
        True
        >>> GWTValidator.validate_docid("invalid")
        False
        >>> 
        >>> GWTValidator.validate_dossier("101 2025 370")
        True
        >>> GWTValidator.validate_dossier("java.util.HashMap/1797211028")
        False  # Contamination détectée!
    """
    
    @staticmethod
    def validate_docid(value: Optional[str]) -> bool:
        """Vérifie format DocId (32 hex chars).
        
        Args:
            value: Valeur à valider
            
        Returns:
            True si format valide, False sinon
            
        Example:
            >>> GWTValidator.validate_docid("e1b042e30d1c43579449bcce8668b493")
            True
            >>> GWTValidator.validate_docid("abc")
            False
        """
        if not value:
            return False
        
        return bool(RE_DOCID.fullmatch(str(value).strip()))
    
    @staticmethod
    def validate_dossier(value: Optional[str], strict: bool = True) -> bool:
        """Vérifie format numéro de dossier (Num).
        
        Formats valides :
        - "101 2025 370" (espaces)
        - "101_2025_370" (underscores)
        
        Détecte les contaminations :
        - Texte trop long (>40 chars)
        - Patterns Java (java.util.HashMap/...)
        - Phrases (probablement Leitsatz/Rechtsgebiet)
        
        Args:
            value: Valeur à valider
            strict: Si True, rejette aussi les valeurs suspectes (long texte)
            
        Returns:
            True si format valide, False sinon
            
        Example:
            >>> GWTValidator.validate_dossier("101 2025 370")
            True
            >>> GWTValidator.validate_dossier("java.util.HashMap/1797211028")
            False  # Contamination!
            >>> GWTValidator.validate_dossier("Assistance judiciaire, montant...")
            False  # Leitsatz contaminant!
        """
        if not value:
            return False
        
        s = str(value).strip()
        
        # Check format de base
        is_valid_format = (
            RE_DOSSIER_GENERIC.fullmatch(s) or
            RE_DOSSIER_UNDERSCORE.fullmatch(s)
        )
        
        if not is_valid_format:
            return False
        
        # En mode strict, détecter les contaminations
        if strict:
            # Texte trop long (probablement Leitsatz)
            if RE_LONG_TEXT.match(s):
                logger.warning(f"Dossier suspect (too long): {s[:50]}...")
                return False
            
            # Pattern Java
            if RE_JAVA_CLASS.search(s):
                logger.warning(f"Dossier suspect (Java class): {s[:50]}...")
                return False
        
        return True
    
    @staticmethod
    def validate_date(value: Optional[str]) -> bool:
        """Vérifie format date ISO.
        
        Args:
            value: Valeur à valider
            
        Returns:
            True si format ISO valide, False sinon
            
        Example:
            >>> GWTValidator.validate_date("2026-01-30")
            True
            >>> GWTValidator.validate_date("30.01.2026")
            False  # Pas ISO
        """
        if not value:
            return False
        
        return bool(RE_ISO_DATE.fullmatch(str(value).strip()))
    
    @staticmethod
    def validate_pfad(value: Optional[str]) -> bool:
        """Vérifie format chemin PDF (pfad).
        
        Formats valides :
        - "C:\\path\\to\\file.pdf" (Windows)
        - "[hex64 chars]" (nouvelle syntaxe)
        
        Args:
            value: Valeur à valider
            
        Returns:
            True si format valide, False sinon
        """
        if not value:
            return False
        
        s = str(value).strip()
        
        return bool(
            RE_PFAD_WINDOWS.fullmatch(s) or
            RE_PFAD_HEX.fullmatch(s)
        )
    
    @staticmethod
    def sanitize_html(text: Optional[str]) -> str:
        """Nettoie les injections HTML potentielles.
        
        Args:
            text: Texte à nettoyer
            
        Returns:
            Texte échappé HTML-safe
            
        Example:
            >>> GWTValidator.sanitize_html("<script>alert('xss')</script>")
            "&lt;script&gt;alert('xss')&lt;/script&gt;"
        """
        if not text:
            return ''
        
        import html
        return html.escape(str(text))
    
    @staticmethod
    def is_contamination(value: Optional[str], expected_type: str) -> bool:
        """Détecte si une valeur est probablement contaminée.
        
        Args:
            value: Valeur à vérifier
            expected_type: Type attendu ('docid', 'dossier', 'date', 'pfad')
            
        Returns:
            True si contamination détectée, False sinon
            
        Example:
            >>> GWTValidator.is_contamination("java.util.HashMap/...", "dossier")
            True  # Num contaminé par donnée Java!
            >>> GWTValidator.is_contamination("Assistance judiciaire...", "dossier")
            True  # Num contaminé par Leitsatz!
        """
        if not value:
            return False
        
        s = str(value).strip()
        
        # Contamination par classe Java
        if RE_JAVA_CLASS.search(s):
            return True
        
        # Contamination par texte long (selon type attendu)
        if expected_type == 'dossier':
            if len(s) > 40:  # Num devrait être < 20 chars
                return True
        
        if expected_type == 'docid':
            if len(s) != 32:
                return True
        
        if expected_type == 'date':
            if len(s) > 10:  # ISO date = 10 chars
                return True
        
        return False
