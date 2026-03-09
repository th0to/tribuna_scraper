"""Date Parser - Parser centralisé pour tous les formats de date.

Ce module élimine la duplication de code de parsing de dates présente
dans fribourg_spider.py (_parse_date_any, _scan_context_for_dates, etc.)

Formats supportés :
- ISO : 2026-01-30
- CH : 30.01.2026
- Slash : 30/01/2026
- Month name : 30 janvier 2026
"""

import re
import unicodedata
import logging
from datetime import date
from typing import Optional, List, Match

logger = logging.getLogger(__name__)

# Patterns de date
RE_DATE_ISO = re.compile(r'\d{4}-\d{2}-\d{2}')
RE_DATE_CH = re.compile(r'\d{2}\.\d{2}\.\d{4}')
RE_DATE_SLASH = re.compile(r'\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b')
RE_MONTH_NAME = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})\b", re.IGNORECASE)

# Mapping des noms de mois (FR/EN/DE)
MONTH_MAP = {
    'janvier': 1, 'janv': 1, 'jan': 1, 'january': 1,
    'février': 2, 'fevrier': 2, 'fevr': 2, 'february': 2, 'feb': 2,
    'mars': 3, 'mar': 3, 'march': 3,
    'avril': 4, 'avr': 4, 'april': 4, 'apr': 4,
    'mai': 5, 'may': 5,
    'juin': 6, 'jun': 6, 'june': 6,
    'juillet': 7, 'jul': 7, 'july': 7,
    'août': 8, 'aout': 8, 'aug': 8, 'august': 8,
    'septembre': 9, 'sept': 9, 'september': 9,
    'octobre': 10, 'oct': 10, 'october': 10,
    'novembre': 11, 'nov': 11, 'november': 11,
    'décembre': 12, 'decembre': 12, 'dec': 12, 'december': 12,
}


class DateParser:
    """Parser centralisé pour tous les formats de date.
    
    Cette classe unifie la logique de parsing de dates qui était dispersée
    dans plusieurs méthodes du spider (_parse_date_any, _parse_slash_date, etc.)
    
    Example:
        >>> parser = DateParser()
        >>> parser.parse_any("2026-01-30")
        date(2026, 1, 30)
        >>> parser.parse_any("30.01.2026")
        date(2026, 1, 30)
        >>> parser.parse_any("30 janvier 2026")
        date(2026, 1, 30)
        >>> parser.scan_all_in_text("Doc du 2026-01-15 publié 2026-01-30")
        [date(2026, 1, 15), date(2026, 1, 30)]
    """
    
    def __init__(self):
        """Initialize parser avec les patterns de date."""
        self.patterns = [
            (RE_DATE_ISO, self._parse_iso_match),
            (RE_DATE_CH, self._parse_ch_match),
            (RE_DATE_SLASH, self._parse_slash_match),
            (RE_MONTH_NAME, self._parse_month_name_match),
        ]
    
    def parse_any(self, value) -> Optional[date]:
        """Parse n'importe quel format de date.
        
        Args:
            value: Valeur à parser (str, date, datetime, ou autre)
            
        Returns:
            date object ou None si parsing échoue
            
        Example:
            >>> parser.parse_any("2026-01-30")
            date(2026, 1, 30)
            >>> parser.parse_any(date(2026, 1, 30))
            date(2026, 1, 30)
            >>> parser.parse_any("invalid")
            None
        """
        # Déjà une date
        if isinstance(value, date):
            return value
        
        # datetime → date
        if hasattr(value, 'date'):
            return value.date()
        
        # Convertir en string
        if value is None:
            return None
        
        s = str(value).strip()
        if not s:
            return None
        
        # Normaliser (NFKC pour accents)
        s = self._normalize(s)
        
        # Essayer chaque pattern
        for pattern, parser_func in self.patterns:
            if match := pattern.fullmatch(s):
                try:
                    return parser_func(match)
                except (ValueError, IndexError) as e:
                    logger.debug(f"Parse failed for '{s}' with pattern {pattern.pattern}: {e}")
                    continue
        
        return None
    
    def scan_all_in_text(self, text: str) -> List[date]:
        """Trouve toutes les dates dans un texte.
        
        Args:
            text: Texte à scanner
            
        Returns:
            Liste de date objects (peut contenir des doublons)
            
        Example:
            >>> parser.scan_all_in_text("Publié le 2026-01-30, décision du 15.01.2026")
            [date(2026, 1, 30), date(2026, 1, 15)]
        """
        if not text:
            return []
        
        text = self._normalize(text)
        results = []
        
        # Scanner avec chaque pattern
        for pattern, parser_func in self.patterns:
            for match in pattern.finditer(text):
                try:
                    d = parser_func(match)
                    if d:
                        results.append(d)
                except (ValueError, IndexError):
                    continue
        
        return results
    
    def _normalize(self, text: str) -> str:
        """Normalise le texte (NFKC pour accents)."""
        try:
            return unicodedata.normalize('NFKC', text or '')
        except Exception:
            return text or ''
    
    def _parse_iso_match(self, match: Match) -> Optional[date]:
        """Parse une date ISO : 2026-01-30"""
        s = match.group(0) if isinstance(match, Match) else str(match)
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    
    def _parse_ch_match(self, match: Match) -> Optional[date]:
        """Parse une date CH : 30.01.2026"""
        s = match.group(0) if isinstance(match, Match) else str(match)
        parts = s.split('.')
        if len(parts) != 3:
            return None
        try:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return date(year, month, day)
        except ValueError:
            return None
    
    def _parse_slash_match(self, match: Match) -> Optional[date]:
        """Parse une date slash : 30/01/2026 ou 30-01-2026"""
        try:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            
            # Validation basique
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            
            return date(year, month, day)
        except (ValueError, IndexError):
            return None
    
    def _parse_month_name_match(self, match: Match) -> Optional[date]:
        """Parse une date avec nom de mois : 30 janvier 2026"""
        try:
            day = int(match.group(1))
            month_name = match.group(2).lower().strip('.')
            year = int(match.group(3))
            
            # Lookup mois
            month = MONTH_MAP.get(month_name)
            if not month:
                return None
            
            # Validation
            if not (1 <= day <= 31):
                return None
            
            return date(year, month, day)
        except (ValueError, IndexError):
            return None
