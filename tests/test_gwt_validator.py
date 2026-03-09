"""Tests unitaires pour GWTValidator.

Ces tests vérifient la validation des données GWT pour détecter
les erreurs de parsing (contamination, offsets incorrects, etc.)
"""

import pytest
from publication_scraper.lib.gwt_validator import GWTValidator


class TestValidateDocId:
    """Tests pour validation DocId."""
    
    def test_valid_docid_lowercase(self):
        """DocId 32 hex chars lowercase valide."""
        assert GWTValidator.validate_docid("e1b042e30d1c43579449bcce8668b493")
    
    def test_valid_docid_uppercase(self):
        """DocId 32 hex chars uppercase valide."""
        assert GWTValidator.validate_docid("E1B042E30D1C43579449BCCE8668B493")
    
    def test_valid_docid_mixed(self):
        """DocId 32 hex chars mixed case valide."""
        assert GWTValidator.validate_docid("E1b042e30D1c43579449BCce8668b493")
    
    def test_invalid_docid_too_short(self):
        """Rejette DocId trop court."""
        assert not GWTValidator.validate_docid("abc123")
    
    def test_invalid_docid_too_long(self):
        """Rejette DocId trop long."""
        assert not GWTValidator.validate_docid("e1b042e30d1c43579449bcce8668b493extra")
    
    def test_invalid_docid_non_hex(self):
        """Rejette DocId avec caractères non-hex."""
        assert not GWTValidator.validate_docid("g1b042e30d1c43579449bcce8668b493")
    
    def test_invalid_docid_none(self):
        """Rejette None."""
        assert not GWTValidator.validate_docid(None)
    
    def test_invalid_docid_empty(self):
        """Rejette string vide."""
        assert not GWTValidator.validate_docid("")


class TestValidateDossier:
    """Tests pour validation numéro de dossier (Num)."""
    
    def test_valid_dossier_spaces(self):
        """Num avec espaces valide."""
        assert GWTValidator.validate_dossier("101 2025 370")
    
    def test_valid_dossier_underscores(self):
        """Num avec underscores valide."""
        assert GWTValidator.validate_dossier("101_2025_370")
    
    def test_valid_dossier_3_digits_prefix(self):
        """Num avec préfixe 3 chiffres."""
        assert GWTValidator.validate_dossier("502 2025 86")
    
    def test_valid_dossier_4_digits_prefix(self):
        """Num avec préfixe 4 chiffres."""
        assert GWTValidator.validate_dossier("1024 2025 1")
    
    def test_invalid_dossier_java_hashmap(self):
        """Rejette contamination par HashMap (cas réel)."""
        assert not GWTValidator.validate_dossier("java.util.HashMap/1797211028")
    
    def test_invalid_dossier_leitsatz(self):
        """Rejette contamination par Leitsatz (texte long)."""
        leitsatz = "Assistance judiciaire, montant de l'indemnité en matière civile"
        assert not GWTValidator.validate_dossier(leitsatz, strict=True)
    
    def test_invalid_dossier_rechtsgebiet(self):
        """Rejette contamination par Rechtsgebiet."""
        rechtsgebiet = "Assurance-accidents - notion d'invalidité - principe d'uniformité"
        assert not GWTValidator.validate_dossier(rechtsgebiet, strict=True)
    
    def test_invalid_dossier_wrong_format(self):
        """Rejette format incorrect."""
        assert not GWTValidator.validate_dossier("ABC-123-456")
    
    def test_invalid_dossier_none(self):
        """Rejette None."""
        assert not GWTValidator.validate_dossier(None)


class TestValidateDate:
    """Tests pour validation date ISO."""
    
    def test_valid_date_iso(self):
        """Date ISO valide."""
        assert GWTValidator.validate_date("2026-01-30")
    
    def test_invalid_date_ch(self):
        """Rejette format CH (pas ISO)."""
        assert not GWTValidator.validate_date("30.01.2026")
    
    def test_invalid_date_slash(self):
        """Rejette format slash (pas ISO)."""
        assert not GWTValidator.validate_date("30/01/2026")
    
    def test_invalid_date_none(self):
        """Rejette None."""
        assert not GWTValidator.validate_date(None)


class TestValidatePfad:
    """Tests pour validation chemin PDF."""
    
    def test_valid_pfad_windows(self):
        """Chemin Windows valide."""
        assert GWTValidator.validate_pfad("C:\\Tribunal\\Decisions\\2026\\file.pdf")
    
    def test_valid_pfad_hex(self):
        """Hash hex64+ valide (nouvelle syntaxe)."""
        assert GWTValidator.validate_pfad("a" * 64)
    
    def test_invalid_pfad_relative(self):
        """Rejette chemin relatif."""
        assert not GWTValidator.validate_pfad("../file.pdf")
    
    def test_invalid_pfad_none(self):
        """Rejette None."""
        assert not GWTValidator.validate_pfad(None)


class TestIsContamination:
    """Tests pour détection de contamination."""
    
    def test_contamination_java_class_in_dossier(self):
        """Détecte contamination par classe Java."""
        assert GWTValidator.is_contamination("java.util.HashMap/1797211028", "dossier")
    
    def test_contamination_long_text_in_dossier(self):
        """Détecte contamination par texte long."""
        long_text = "x" * 50
        assert GWTValidator.is_contamination(long_text, "dossier")
    
    def test_contamination_wrong_length_docid(self):
        """Détecte DocId de mauvaise longueur."""
        assert GWTValidator.is_contamination("abc", "docid")
    
    def test_no_contamination_valid_dossier(self):
        """Pas de contamination pour Num valide."""
        assert not GWTValidator.is_contamination("101 2025 370", "dossier")


class TestSanitizeHTML:
    """Tests pour nettoyage HTML."""
    
    def test_sanitize_script_tag(self):
        """Échappe balises script."""
        result = GWTValidator.sanitize_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
    
    def test_sanitize_none(self):
        """Retourne string vide pour None."""
        result = GWTValidator.sanitize_html(None)
        assert result == ''
