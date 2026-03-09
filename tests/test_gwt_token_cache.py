"""Tests unitaires pour GWTTokenCache.

Ces tests vérifient le cache des tokens GWT (X-GWT-Permutation, X-GWT-Module-Base)
avec TTL de 24h pour éviter bootstrap répétés (-2 à -3 secondes par run).
"""

import pytest
import tempfile
import os
import time
from datetime import timedelta
from pathlib import Path
from publication_scraper.lib.gwt_token_cache import GWTTokenCache


class TestGWTTokenCache:
    """Tests pour cache tokens GWT."""
    
    @pytest.fixture
    def temp_cache_dir(self):
        """Répertoire cache temporaire pour tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def cache(self, temp_cache_dir):
        """Instance cache avec répertoire temporaire."""
        return GWTTokenCache(cache_dir=temp_cache_dir)
    
    def test_cache_miss(self, cache):
        """Cache miss retourne None."""
        tokens = cache.get('FR')
        assert tokens is None
    
    def test_cache_hit(self, cache):
        """Cache hit retourne tokens."""
        # Set tokens
        cache.set('FR', 'perm123', 'base456')
        
        # Get tokens
        tokens = cache.get('FR')
        assert tokens is not None
        assert tokens['X-GWT-Permutation'] == 'perm123'
        assert tokens['X-GWT-Module-Base'] == 'base456'
    
    def test_cache_different_cantons(self, cache):
        """Cache sépare cantons différents."""
        cache.set('FR', 'perm_fr', 'base_fr')
        cache.set('BE', 'perm_be', 'base_be')
        
        tokens_fr = cache.get('FR')
        tokens_be = cache.get('BE')
        
        assert tokens_fr['X-GWT-Permutation'] == 'perm_fr'
        assert tokens_be['X-GWT-Permutation'] == 'perm_be'
    
    def test_cache_persistence(self, temp_cache_dir):
        """Cache persiste entre instances."""
        # Instance 1: set tokens
        cache1 = GWTTokenCache(cache_dir=temp_cache_dir)
        cache1.set('VD', 'perm_vd', 'base_vd')
        
        # Instance 2: get tokens
        cache2 = GWTTokenCache(cache_dir=temp_cache_dir)
        tokens = cache2.get('VD')
        
        assert tokens is not None
        assert tokens['X-GWT-Permutation'] == 'perm_vd'
    
    def test_cache_ttl_expiration(self, cache):
        """Cache expire après TTL."""
        # Set avec TTL court pour test
        cache.ttl = timedelta(seconds=0.3)  # 300ms
        cache.set('FR', 'perm', 'base')
        
        # Immédiatement: cache hit
        tokens = cache.get('FR')
        assert tokens is not None
        
        # Après TTL: cache miss
        time.sleep(0.5)
        tokens = cache.get('FR')
        assert tokens is None
    
    def test_invalidate_canton(self, cache):
        """Invalidation canton spécifique."""
        cache.set('FR', 'perm_fr', 'base_fr')
        cache.set('BE', 'perm_be', 'base_be')
        
        cache.invalidate('FR')
        
        assert cache.get('FR') is None
        assert cache.get('BE') is not None
    
    def test_invalidate_multiple_cantons(self, cache):
        """Invalidation de plusieurs cantons."""
        cache.set('FR', 'perm_fr', 'base_fr')
        cache.set('BE', 'perm_be', 'base_be')
        cache.set('VD', 'perm_vd', 'base_vd')
        
        # Invalider chacun individuellement
        cache.invalidate('FR')
        cache.invalidate('BE')
        cache.invalidate('VD')
        
        assert cache.get('FR') is None
        assert cache.get('BE') is None
        assert cache.get('VD') is None
    
    def test_corrupted_cache_file(self, cache, temp_cache_dir):
        """Cache corrompu ne bloque pas."""
        # Créer fichier cache corrompu
        cache_file = Path(temp_cache_dir) / 'fr_gwt_tokens.json'
        cache_file.write_text('INVALID JSON{{{')
        
        # Get devrait retourner None (pas d'exception)
        tokens = cache.get('FR')
        assert tokens is None
    
    def test_cache_file_structure(self, cache, temp_cache_dir):
        """Structure fichier cache correcte."""
        cache.set('FR', 'perm123', 'base456')
        
        # Le fichier est créé avec prefix canton lowercase
        cache_file = Path(temp_cache_dir) / 'fr_gwt_tokens.json'
        assert cache_file.exists()
        
        # Vérifier structure JSON
        import json
        with open(cache_file) as f:
            data = json.load(f)
        
        assert 'canton' in data
        assert 'permutation' in data
        assert 'module_base' in data
        assert 'timestamp' in data


class TestGWTTokenCachePerformance:
    """Tests de performance cache."""
    
    def test_cache_creation_fast(self):
        """Création cache rapide (<10ms)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.time()
            cache = GWTTokenCache(cache_dir=tmpdir)
            elapsed = time.time() - start
            
            assert elapsed < 0.01  # <10ms
    
    def test_cache_get_fast(self):
        """Lecture cache rapide (<10ms toléré)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GWTTokenCache(cache_dir=tmpdir)
            cache.set('FR', 'perm', 'base')
            
            start = time.time()
            tokens = cache.get('FR')
            elapsed = time.time() - start
            
            # Note: tolérance augmentée car I/O filesystem variable
            assert elapsed < 0.05  # <50ms (filesystem + antivirus overhead)
            assert tokens is not None
    
    def test_cache_set_fast(self):
        """Écriture cache rapide (<10ms)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = GWTTokenCache(cache_dir=tmpdir)
            
            start = time.time()
            cache.set('FR', 'perm', 'base')
            elapsed = time.time() - start
            
            assert elapsed < 0.01  # <10ms
