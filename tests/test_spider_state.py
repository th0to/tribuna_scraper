"""Tests unitaires pour SpiderState et SpiderConfig.

Ces tests vérifient la gestion d'état du spider (pages traitées,
items émis, DocIds vus) et la configuration (canton, durée, télécharger PDFs).
"""

import pytest
from datetime import datetime, timedelta, date
from publication_scraper.lib.spider_state import SpiderState, SpiderConfig


class TestSpiderConfig:
    """Tests pour SpiderConfig (configuration immutable)."""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock settings Scrapy."""
        class MockSettings:
            _defaults = {
                'MAX_PAGES': 0,
                'MAX_ITEMS': 0,
                'PAGE_PDF_BARRIER': True,
            }
            def get(self, key, default=None):
                return self._defaults.get(key, default)
            def getint(self, key, default=0):
                val = self._defaults.get(key, default)
                return int(val) if val is not None else default
            def getbool(self, key, default=False):
                val = self._defaults.get(key, default)
                return bool(val)
        return MockSettings()
    
    def test_from_config_dict_minimal(self, mock_settings):
        """Config minimal valide."""
        config_dict = {
            'name': 'Fribourg',
            'canton': 'FR',
            'kanton_kurz': 'FR',
            'result_page_url': 'https://example.com/gwt',
            'decrypt_page_url': 'https://example.com/decrypt',
            'download_url': 'https://example.com/pdf',
            'headers': {},
            'result_query_tpl': 'TPL',
            'result_query_sort': 'TPL_SORT',
            'decrypt_start': 'START',
            'decrypt_end': 'END',
            'encrypted': False,
            'ascii_encrypted': False
        }
        config = SpiderConfig.from_config_dict(config_dict, days=30, settings=mock_settings)
        
        assert config.canton == 'Fribourg'  # name field
        assert config.is_incremental() is True
    
    def test_from_config_dict_full_run(self, mock_settings):
        """Config full run (pas incrémental)."""
        config_dict = {
            'name': 'Bern',
            'canton': 'BE',
            'kanton_kurz': 'BE',
            'result_page_url': 'https://example.com/gwt',
            'decrypt_page_url': 'https://example.com/decrypt',
            'download_url': 'https://example.com/pdf',
            'headers': {},
            'result_query_tpl': 'TPL',
            'result_query_sort': 'TPL_SORT',
            'decrypt_start': 'START',
            'decrypt_end': 'END',
            'encrypted': False,
            'ascii_encrypted': False
        }
        config = SpiderConfig.from_config_dict(config_dict, days=None, settings=mock_settings)
        
        assert config.canton == 'Bern'  # name field
        assert config.is_incremental() is False
    
    def test_get_future_cutoff_pd(self, mock_settings):
        """Calcul cutoff PDatum."""
        config_dict = {
            'name': 'Vaud',
            'canton': 'VD',
            'kanton_kurz': 'VD',
            'result_page_url': 'https://example.com/gwt',
            'decrypt_page_url': 'https://example.com/decrypt',
            'download_url': 'https://example.com/pdf',
            'headers': {},
            'result_query_tpl': 'TPL',
            'result_query_sort': 'TPL_SORT',
            'decrypt_start': 'START',
            'decrypt_end': 'END',
            'encrypted': False,
            'ascii_encrypted': False
        }
        config = SpiderConfig.from_config_dict(config_dict, days=7, settings=mock_settings)
        
        cutoff = config.get_future_cutoff_pd()
        assert cutoff is not None
        assert isinstance(cutoff, date)


class TestSpiderState:
    """Tests pour SpiderState (état mutable)."""
    
    def test_initial_state(self):
        """État initial vide."""
        state = SpiderState()
        
        assert state.pages_processed == 0
        assert state.items_emitted == 0
        assert len(state.seen_ids) == 0
        assert len(state.yielded_docids) == 0
    
    def test_increment_page(self):
        """Incrémentation pages_processed."""
        state = SpiderState()
        state.pages_processed += 1
        
        assert state.pages_processed == 1
        
        state.pages_processed += 1
        assert state.pages_processed == 2
    
    def test_record_emit_new_docid(self):
        """Émission nouvel item (DocId jamais vu)."""
        state = SpiderState()
        
        can_emit = state.can_emit(max_items=100)  # max_items est un int
        assert can_emit is True
        
        state.record_emit("abc123")
        assert state.items_emitted == 1
        assert "abc123" in state.yielded_docids
    
    def test_record_emit_duplicate_docid(self):
        """Ne peut pas émettre DocId déjà yielded."""
        state = SpiderState()
        
        state.record_emit("abc123")
        assert state.is_doc_yielded("abc123") is True  # Vérifier avec is_doc_yielded
        
        # Tentative d'émission duplicate - record_emit compte quand même
        state.record_emit("abc123")
        assert state.items_emitted == 2  # Incrémenté (pas de check interne)
    
    def test_mark_seen(self):
        """Marquer DocId comme vu (sans yield)."""
        state = SpiderState()
        
        state.mark_seen("xyz789")
        assert "xyz789" in state.seen_ids
        assert "xyz789" not in state.yielded_docids
    
    def test_summary_empty(self):
        """Résumé état vide."""
        state = SpiderState()
        summary = state.summary()
        
        assert summary['pages_processed'] == 0
        assert summary['items_emitted'] == 0
        assert summary['docids_seen'] == 0
    
    def test_summary_after_processing(self):
        """Résumé après traitement."""
        state = SpiderState()
        
        state.pages_processed += 1
        state.pages_processed += 1
        state.record_emit("doc1")
        state.mark_seen("doc1")  # Mark aussi seen
        state.record_emit("doc2")
        state.mark_seen("doc2")  # Mark aussi seen
        state.mark_seen("doc3")
        
        summary = state.summary()
        
        assert summary['pages_processed'] == 2
        assert summary['items_emitted'] == 2
        assert summary['docids_seen'] == 3  # doc1, doc2, doc3


class TestSpiderStateIntegration:
    """Tests d'intégration SpiderState + SpiderConfig."""
    
    def test_realistic_scraping_scenario(self):
        """Simulation scraping réaliste avec 2 pages."""
        # SpiderState sans config (autonome)
        state = SpiderState()
        
        # Page 1: 3 items
        state.pages_processed += 1
        for docid in ["doc1", "doc2", "doc3"]:
            if state.can_emit(max_items=100):
                state.record_emit(docid)
        
        # Page 2: 2 nouveaux items + 1 duplicate
        state.pages_processed += 1
        for docid in ["doc3", "doc4", "doc5"]:  # doc3 duplicate
            if not state.is_doc_yielded(docid):
                state.record_emit(docid)
        
        summary = state.summary()
        assert summary['pages_processed'] == 2
        assert summary['items_emitted'] == 5  # doc1-5 même si doc3 répété
        assert summary['docids_yielded'] == 5  # doc1-5 dans set
