"""Date Detector - Détection des dates de publication (PDatum) et décision (EDatum).

Implémente uniquement la stratégie "last_in_row" utilisée par Fribourg:
- Sélectionne la dernière date valide dans la ligne GWT
- Exclut la date de décision (EDatum) si elle est identique
- Filtre les dates futures invalides
"""

import re
import unicodedata
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Patterns de date
RE_DATE_ISO = re.compile(r'\d{4}-\d{2}-\d{2}')
RE_DATE_CH = re.compile(r'\d{2}\.\d{2}\.\d{4}')
RE_DATE_SLASH = re.compile(r'\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b')
RE_MONTH_NAME = re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})\b", re.IGNORECASE)

# Mapping des noms de mois
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


class DateDetector:
    """Détecteur de dates pour Fribourg (stratégie last_in_row).
    
    Cette classe simplifie la détection de dates par rapport au spider original
    en ne conservant que la stratégie utilisée par Fribourg.
    """
    
    def __init__(
        self,
        pdatum_future_days: int = 0,
        edatum_future_days: int = 14,
        min_date: Optional[date] = None,
        max_date: Optional[date] = None,
    ):
        """
        Args:
            pdatum_future_days: Tolérance jours futurs pour PDatum (0 = strict)
            edatum_future_days: Tolérance jours futurs pour EDatum
            min_date: Date minimum pour filtrage incrémental
            max_date: Date maximum (généralement aujourd'hui)
        """
        self.pdatum_future_days = pdatum_future_days
        self.edatum_future_days = edatum_future_days
        self.min_date = min_date
        self.max_date = max_date
        
        # Calculer les cutoffs
        self.today = datetime.now(timezone.utc).date()
        self.future_cutoff_pd = self.today + timedelta(days=pdatum_future_days)
        self.future_cutoff_ed = self.today + timedelta(days=edatum_future_days)
        
        # Ajuster avec max_date si fournie
        if max_date and max_date < self.future_cutoff_pd:
            self.future_cutoff_pd = max_date
            
    def detect_dates(
        self,
        tokens: List[str],
        id_pos: int,
        row_end_hint: Optional[int] = None,
        doc_id: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Détecte EDatum et PDatum dans une ligne de tokens.
        
        Stratégie "last_in_row" pour Fribourg:
        1. Trouver EDatum près de l'ID (offset +3)
        2. Trouver PDatum = dernière date valide de la ligne (excluant EDatum)
        
        Args:
            tokens: Tokens de la ligne GWT
            id_pos: Position du DocId dans les tokens
            row_end_hint: Fin estimée de la ligne (optionnel)
            doc_id: DocId pour logging (optionnel)
            
        Returns:
            Tuple (edatum_iso, pdatum_iso) - chaînes vides si non trouvées
        """
        edatum = ""
        pdatum = ""
        
        # Déterminer la fin de ligne
        row_end = row_end_hint if isinstance(row_end_hint, int) and row_end_hint > 0 else len(tokens)
        row_end = min(row_end, len(tokens))
        
        # --- Détection EDatum (date de décision) ---
        base_idx = id_pos + 3
        edatum = self._find_edatum(tokens, base_idx, row_end)
        
        # Filtrer EDatum futur
        if edatum:
            ed_obj = self._parse_date(edatum)
            if ed_obj and ed_obj > self.future_cutoff_ed:
                edatum = ""
                
        # --- Détection PDatum (stratégie last_in_row) ---
        pdatum = self._find_pdatum_last_in_row(tokens, id_pos, row_end, edatum)
        
        return edatum, pdatum
    
    def _find_edatum(self, tokens: List[str], base_idx: int, row_end: int) -> str:
        """Trouve la date de décision (EDatum) près de l'ID."""
        # Essayer base_idx et base_idx+1
        for offset in [0, 1]:
            idx = base_idx + offset
            if idx < row_end:
                d = self._parse_date(tokens[idx])
                if d:
                    return d.isoformat()
                    
        # Scan élargi si non trouvé
        for off in range(0, min(20, row_end - base_idx)):
            idx = base_idx + off
            if idx < row_end:
                d = self._parse_date(tokens[idx])
                if d:
                    return d.isoformat()
                    
        return ""
    
    def _find_pdatum_last_in_row(
        self, 
        tokens: List[str], 
        id_pos: int, 
        row_end: int,
        edatum: str
    ) -> str:
        """Trouve PDatum avec la stratégie last_in_row.
        
        Parcourt la ligne depuis la fin et prend la première date valide
        qui n'est pas l'EDatum.
        """
        for idx in range(row_end - 1, max(-1, id_pos - 1), -1):
            d = self._parse_date(tokens[idx])
            if d is None:
                continue
                
            # Vérifier le cutoff futur
            if d > self.future_cutoff_pd:
                continue
                
            iso = d.isoformat()
            
            # Exclure si identique à EDatum
            if edatum and iso == edatum:
                continue
                
            return iso
            
        return ""
    
    def _parse_date(self, value: str) -> Optional[date]:
        """Parse une chaîne en date (ISO, CH, ou avec nom de mois)."""
        if not value:
            return None
            
        value = value.strip()
        if not value:
            return None
            
        # Format ISO: YYYY-MM-DD
        if RE_DATE_ISO.fullmatch(value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
                
        # Format CH: DD.MM.YYYY
        if RE_DATE_CH.fullmatch(value):
            try:
                parts = value.split('.')
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except (ValueError, IndexError):
                pass
                
        # Format avec séparateurs: DD/MM/YYYY, DD-MM-YYYY
        m = RE_DATE_SLASH.fullmatch(value)
        if m:
            try:
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # Valider les plages
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return date(year, month, day)
            except ValueError:
                pass
                
        # Format avec nom de mois: "15 janvier 2024"
        m = RE_MONTH_NAME.search(self._normalize(value))
        if m:
            try:
                day = int(m.group(1))
                month_name = m.group(2).lower().strip('.')
                year = int(m.group(3))
                month = MONTH_MAP.get(month_name)
                if month and 1 <= day <= 31:
                    return date(year, month, day)
            except ValueError:
                pass
                
        return None
    
    def _normalize(self, text: str) -> str:
        """Normalise le texte (NFKC)."""
        try:
            return unicodedata.normalize('NFKC', text or '')
        except Exception:
            return text or ''
    
    def is_date_eligible(self, date_str: str) -> bool:
        """Vérifie si une date est dans la plage [min_date, max_date]."""
        if not date_str:
            return False
            
        d = self._parse_date(date_str)
        if d is None:
            return False
            
        if self.min_date and d < self.min_date:
            return False
            
        if self.max_date and d > self.max_date:
            return False
            
        return True
    
    def extract_date_from_text(self, text: str) -> Optional[str]:
        """Extrait une date depuis du texte brut (fallback).
        
        Utile quand les tokens ne contiennent pas la date mais qu'elle
        est présente dans le contenu brut près du DocId.
        """
        if not text:
            return None
            
        # Chercher format ISO d'abord
        m = RE_DATE_ISO.search(text)
        if m:
            d = self._parse_date(m.group())
            if d:
                return d.isoformat()
                
        # Chercher format CH
        m = RE_DATE_CH.search(text)
        if m:
            d = self._parse_date(m.group())
            if d:
                return d.isoformat()
                
        # Chercher format avec nom de mois
        m = RE_MONTH_NAME.search(self._normalize(text))
        if m:
            try:
                day = int(m.group(1))
                month_name = m.group(2).lower().strip('.')
                year = int(m.group(3))
                month = MONTH_MAP.get(month_name)
                if month and 1 <= day <= 31:
                    return date(year, month, day).isoformat()
            except ValueError:
                pass
                
        return None
