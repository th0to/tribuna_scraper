"""Tests unitaires pour DateParser.

Ces tests vérifient que le parsing de dates fonctionne correctement
pour tous les formats supportés (ISO, CH, slash, month name).
"""

import pytest
from datetime import date
from publication_scraper.lib.date_parser import DateParser


class TestDateParserISO:
    """Tests pour format ISO (YYYY-MM-DD)."""
    
    def test_parse_iso_valid(self):
        """Parse une date ISO valide."""
        parser = DateParser()
        result = parser.parse_any("2026-01-30")
        assert result == date(2026, 1, 30)
    
    def test_parse_iso_invalid_month(self):
        """Rejette mois invalide."""
        parser = DateParser()
        result = parser.parse_any("2026-13-01")
        assert result is None
    
    def test_parse_iso_invalid_day(self):
        """Rejette jour invalide."""
        parser = DateParser()
        result = parser.parse_any("2026-01-32")
        assert result is None


class TestDateParserCH:
    """Tests pour format CH (DD.MM.YYYY)."""
    
    def test_parse_ch_valid(self):
        """Parse une date CH valide."""
        parser = DateParser()
        result = parser.parse_any("30.01.2026")
        assert result == date(2026, 1, 30)
    
    def test_parse_ch_single_digit(self):
        """Parse avec jour/mois à 1 chiffre."""
        parser = DateParser()
        result = parser.parse_any("5.2.2026")
        assert result == date(2026, 2, 5)


class TestDateParserSlash:
    """Tests pour format slash (DD/MM/YYYY)."""
    
    def test_parse_slash_valid(self):
        """Parse une date slash valide."""
        parser = DateParser()
        result = parser.parse_any("30/01/2026")
        assert result == date(2026, 1, 30)
    
    def test_parse_hyphen_as_slash(self):
        """Parse avec tirets au lieu de slashes."""
        parser = DateParser()
        result = parser.parse_any("30-01-2026")
        assert result == date(2026, 1, 30)


class TestDateParserMonthName:
    """Tests pour format month name (DD mois YYYY)."""
    
    def test_parse_month_name_french(self):
        """Parse avec nom de mois français."""
        parser = DateParser()
        result = parser.parse_any("30 janvier 2026")
        assert result == date(2026, 1, 30)
    
    def test_parse_month_name_abbreviated(self):
        """Parse avec nom de mois abrégé."""
        parser = DateParser()
        result = parser.parse_any("30 janv 2026")
        assert result == date(2026, 1, 30)
    
    def test_parse_month_name_english(self):
        """Parse avec nom de mois anglais."""
        parser = DateParser()
        result = parser.parse_any("30 January 2026")
        assert result == date(2026, 1, 30)


class TestDateParserAlreadyDate:
    """Tests pour objets date déjà parsés."""
    
    def test_parse_date_object(self):
        """Retourne l'objet date tel quel."""
        parser = DateParser()
        d = date(2026, 1, 30)
        result = parser.parse_any(d)
        assert result == d


class TestDateParserEdgeCases:
    """Tests pour cas limites."""
    
    def test_parse_none(self):
        """Retourne None pour None."""
        parser = DateParser()
        result = parser.parse_any(None)
        assert result is None
    
    def test_parse_empty_string(self):
        """Retourne None pour string vide."""
        parser = DateParser()
        result = parser.parse_any("")
        assert result is None
    
    def test_parse_invalid_format(self):
        """Retourne None pour format inconnu."""
        parser = DateParser()
        result = parser.parse_any("not a date")
        assert result is None


class TestDateParserScanText:
    """Tests pour scan_all_in_text()."""
    
    def test_scan_multiple_dates(self):
        """Trouve toutes les dates dans un texte."""
        parser = DateParser()
        text = "Publié le 2026-01-30, décision du 15.01.2026"
        result = parser.scan_all_in_text(text)
        
        # scan_all_in_text peut retourner duplicates si plusieurs patterns matchent
        # On vérifie juste qu'on a au moins les 2 dates différentes
        unique_dates = set(result)
        assert len(unique_dates) == 2
        assert date(2026, 1, 30) in unique_dates
        assert date(2026, 1, 15) in unique_dates
    
    def test_scan_no_dates(self):
        """Retourne liste vide si pas de dates."""
        parser = DateParser()
        text = "Aucune date ici"
        result = parser.scan_all_in_text(text)
        
        assert result == []
    
    def test_scan_mixed_formats(self):
        """Trouve dates de différents formats."""
        parser = DateParser()
        text = "ISO: 2026-01-30, CH: 30.01.2026, Texte: 30 janvier 2026"
        result = parser.scan_all_in_text(text)
        
        # Toutes les 3 sont la même date, mais détectées plusieurs fois
        unique_dates = set(result)
        assert len(unique_dates) == 1  # Une seule date unique
        assert date(2026, 1, 30) in unique_dates
