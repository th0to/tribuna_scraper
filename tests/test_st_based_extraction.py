"""Tests for ST-based PDatum extraction integrated into FribourgSpider.

These tests validate the string-table-based algorithm.

Integration tests and accuracy tests require saved GWT pages.
Use `python tools/fetch_gwt_pages.py --pages 0-7 --outdir saved_pages/fresh`
to fetch fresh pages, then re-run the tests.

Without saved pages, integration/accuracy tests are skipped automatically.
Unit tests (TestExtractStringTable.test_invalid_payload) always run.

Test strategy:
1. Unit tests: _st_extract_items on known ST structures
2. Integration tests: _extract_pdatum_from_string_table on real GWT pages
3. Accuracy tests: validate against ground truth across all saved pages
4. Regression tests: ensure the new code doesn't break extract_tokens/find_docid_indexes
"""

import re
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Helpers to instantiate a minimal FribourgSpider without Scrapy crawler
# ---------------------------------------------------------------------------

import json
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from publication_scraper.spiders import gwt_utils


def _load_ground_truth(path: Path) -> dict:
    """Load ground truth from PDatumListe.updated.txt, returns {num: {pdatum, edatum, docid}}."""
    gt = {}
    re_num = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')

    def conv(d: str) -> str:
        try:
            dd, mm, yy = d.strip().split('.')
            return f'{yy}-{mm}-{dd}'
        except Exception:
            return d.strip()

    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or not line[0].isdigit():
                continue
            parts = [p.strip() for p in line.split(';')]
            if len(parts) < 4:
                continue
            pd, ed, num, docid = parts[:4]
            if not re_num.match(num):
                continue
            gt[num] = {'pdatum': conv(pd), 'edatum': conv(ed), 'docid': docid}
            gt[docid] = {'pdatum': conv(pd), 'edatum': conv(ed), 'num': num}
    return gt


def _make_spider():
    """Create a minimal FribourgSpider instance for testing (no Scrapy crawler)."""
    from publication_scraper.spiders.fribourg_spider import FribourgSpider

    # Monkey-patch __init__ to avoid Scrapy machinery
    spider = FribourgSpider.__new__(FribourgSpider)
    spider.name = 'fribourg'
    spider.today = date(2026, 3, 5)
    spider.min_date = None
    spider.max_date = None
    spider.days = None
    spider.pdatum_future_days = 0
    spider.edatum_future_days = 14
    spider.future_cutoff_pd = spider.today + timedelta(days=spider.pdatum_future_days)
    spider.future_cutoff_ed = spider.today + timedelta(days=spider.edatum_future_days)
    spider.kanton_kurz = 'FR'
    spider.canton = 'fribourg'
    return spider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAVED_PAGES_DIR = PROJECT_ROOT / 'saved_pages' / 'fresh'
GT_PATH = PROJECT_ROOT / 'readme' / 'PDatumListe.updated.txt'


@pytest.fixture(scope='module')
def spider():
    return _make_spider()


@pytest.fixture(scope='module')
def ground_truth():
    if GT_PATH.exists():
        return _load_ground_truth(GT_PATH)
    pytest.skip("Ground truth file not found")


@pytest.fixture(scope='module')
def gwt_pages():
    """Load all saved GWT pages as list of (page_nr, raw_text)."""
    pages = []
    for i in range(8):
        p = SAVED_PAGES_DIR / f'gwt_page{i}.txt'
        if p.exists():
            pages.append((i, p.read_text(encoding='utf-8')))
    if not pages:
        pytest.skip("No saved GWT pages found in saved_pages/fresh/")
    return pages


# ===========================================================================
# 1. Unit tests: gwt_utils.extract_string_table
# ===========================================================================

class TestExtractStringTable:
    """Test the public extract_string_table wrapper."""

    def test_valid_payload(self, gwt_pages):
        """extract_string_table returns a non-empty list for valid GWT pages."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        assert st is not None
        assert len(st) >= 50  # Typical page has 147-177 ST entries

    def test_contains_docids(self, gwt_pages):
        """String table must contain DocIds (32 hex chars)."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        re_docid = re.compile(r'^[0-9a-f]{32}$')
        docids = [s for s in st if re_docid.match(s)]
        assert len(docids) >= 10  # At least 10 DocIds per page

    def test_contains_dates(self, gwt_pages):
        """String table must contain ISO dates."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        re_date = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
        dates = [s for s in st if re_date.match(s)]
        assert len(dates) >= 5  # Multiple dates per page

    def test_contains_nums(self, gwt_pages):
        """String table must contain case numbers (NUM)."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        re_num = re.compile(r'^\d{1,4}\s+\d{4}\s+\d+$')
        nums = [s for s in st if re_num.match(s)]
        assert len(nums) >= 10

    def test_invalid_payload(self):
        """Returns None for invalid payloads."""
        assert gwt_utils.extract_string_table("garbage data") is None
        assert gwt_utils.extract_string_table("") is None
        assert gwt_utils.extract_string_table("//OK[1]") is None


# ===========================================================================
# 2. Unit tests: _st_extract_items
# ===========================================================================

class TestStExtractItems:
    """Test the core ST-based extraction algorithm."""

    def test_extracts_20_items(self, spider, gwt_pages):
        """Each page should yield ~20 items."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        assert len(items) == 20

    def test_all_items_have_docid_and_num(self, spider, gwt_pages):
        """Every extracted item must have docid and num."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        for item in items:
            assert item['docid'], f"Item missing docid: {item}"
            assert item['num'], f"Item missing num: {item}"
            assert len(item['docid']) == 32
            assert re.match(r'^\d{1,4}\s+\d{4}\s+\d+$', item['num'])

    def test_all_items_have_pdatum(self, spider, gwt_pages):
        """All items should have a PDatum (possibly inherited)."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        for item in items:
            assert item['pdatum'], f"Item {item['num']} has no PDatum"

    def test_pdatum_monotonically_decreasing(self, spider, gwt_pages):
        """PDatum values across items should be non-increasing (monotone ≥ in time order)."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        prev_pd = None
        for item in items:
            pd = item['pdatum']
            if pd and prev_pd:
                assert pd <= prev_pd, (
                    f"PDatum increased: {prev_pd} -> {pd} at item {item['num']}"
                )
            if pd:
                prev_pd = pd

    def test_pdatum_source_values(self, spider, gwt_pages):
        """pdatum_source should be one of the expected values."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        valid_sources = {'st_wave_change', 'st_wave_inherit', 'st_explicit', 'none'}
        for item in items:
            assert item['pdatum_source'] in valid_sources, (
                f"Unexpected source: {item['pdatum_source']}"
            )

    def test_at_least_one_wave_change(self, spider, gwt_pages):
        """Each page should have at least one wave_change (the first item)."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        wave_changes = [it for it in items if it['pdatum_source'] == 'st_wave_change']
        assert len(wave_changes) >= 1

    def test_edatum_format(self, spider, gwt_pages):
        """EDatum, when present, must be a valid ISO date."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        items = spider._st_extract_items(st)
        re_date = re.compile(r'^(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$')
        for item in items:
            if item['edatum']:
                assert re_date.match(item['edatum']), (
                    f"Invalid EDatum format: {item['edatum']} for {item['num']}"
                )


# ===========================================================================
# 3. Integration test: _extract_pdatum_from_string_table
# ===========================================================================

class TestExtractPdatumFromStringTable:
    """Test the spider-level method that returns {docid: {pdatum, edatum, ...}}."""

    def test_returns_dict(self, spider, gwt_pages):
        """Should return a dict with DocId keys."""
        _, raw = gwt_pages[0]
        result = spider._extract_pdatum_from_string_table(raw)
        assert isinstance(result, dict)
        assert len(result) >= 10

    def test_all_entries_have_pdatum(self, spider, gwt_pages):
        """Every entry in the result should have a non-empty pdatum."""
        _, raw = gwt_pages[0]
        result = spider._extract_pdatum_from_string_table(raw)
        for docid, info in result.items():
            assert info['pdatum'], f"DocId {docid[:16]}... has no PDatum"

    def test_matches_page0_ground_truth(self, spider, gwt_pages, ground_truth):
        """Page 0 items should match ground truth PDatum."""
        _, raw = gwt_pages[0]
        result = spider._extract_pdatum_from_string_table(raw)

        correct = 0
        total = 0
        for docid, info in result.items():
            gt = ground_truth.get(docid)
            if not gt:
                continue
            total += 1
            if info['pdatum'] == gt['pdatum']:
                correct += 1

        if total > 0:
            accuracy = correct / total
            assert accuracy >= 0.90, (
                f"Page 0 accuracy too low: {correct}/{total} = {accuracy:.1%}"
            )


# ===========================================================================
# 4. Accuracy test across ALL saved pages
# ===========================================================================

class TestAccuracyAllPages:
    """Validate PDatum accuracy across all 8 saved pages against ground truth."""

    def test_overall_accuracy(self, spider, gwt_pages, ground_truth):
        """Overall PDatum accuracy should be >= 95% on fresh data."""
        total_correct = 0
        total_items = 0
        errors = []

        for page_nr, raw in gwt_pages:
            result = spider._extract_pdatum_from_string_table(raw)
            for docid, info in result.items():
                gt = ground_truth.get(docid)
                if not gt:
                    continue
                total_items += 1
                if info['pdatum'] == gt['pdatum']:
                    total_correct += 1
                else:
                    errors.append({
                        'page': page_nr,
                        'docid': docid[:16],
                        'num': info.get('num', '?'),
                        'extracted': info['pdatum'],
                        'expected': gt['pdatum'],
                        'source': info.get('pdatum_source', '?'),
                    })

        if total_items == 0:
            pytest.skip("No items matched ground truth")

        accuracy = total_correct / total_items
        print(f"\nOverall PDatum accuracy: {total_correct}/{total_items} = {accuracy:.1%}")
        if errors:
            print(f"Errors ({len(errors)}):")
            for e in errors:
                print(f"  Page {e['page']}: {e['num']} extracted={e['extracted']} expected={e['expected']} ({e['source']})")

        assert accuracy >= 0.95, (
            f"Overall accuracy too low: {total_correct}/{total_items} = {accuracy:.1%}. "
            f"Errors: {errors}"
        )

    def test_items_per_page(self, spider, gwt_pages):
        """Each page should extract exactly 20 items."""
        for page_nr, raw in gwt_pages:
            result = spider._extract_pdatum_from_string_table(raw)
            assert len(result) == 20, (
                f"Page {page_nr}: expected 20 items, got {len(result)}"
            )

    def test_no_future_pdatum(self, spider, gwt_pages):
        """No extracted PDatum should be in the future (> scrape date + 5 days)."""
        future_limit = spider.today + timedelta(days=5)
        for page_nr, raw in gwt_pages:
            result = spider._extract_pdatum_from_string_table(raw)
            for docid, info in result.items():
                pd = info['pdatum']
                if pd:
                    pd_date = date.fromisoformat(pd)
                    assert pd_date <= future_limit, (
                        f"Page {page_nr}: future PDatum {pd} for {docid[:16]}"
                    )


# ===========================================================================
# 5. Regression tests: existing gwt_utils functions still work
# ===========================================================================

class TestGwtUtilsRegression:
    """Ensure existing gwt_utils functions are not broken by our changes."""

    def test_extract_tokens_non_empty(self, gwt_pages):
        """extract_tokens should still return tokens from valid GWT pages."""
        _, raw = gwt_pages[0]
        tokens = gwt_utils.extract_tokens(raw)
        assert len(tokens) >= 50

    def test_extract_trefferzahl(self, gwt_pages):
        """extract_trefferzahl should still extract the total count."""
        _, raw = gwt_pages[0]
        tz = gwt_utils.extract_trefferzahl(raw)
        assert tz is not None
        assert tz > 0

    def test_find_docid_indexes(self, gwt_pages):
        """find_docid_indexes should still find DocIds in tokens."""
        _, raw = gwt_pages[0]
        tokens = gwt_utils.extract_tokens(raw)
        re_id = re.compile(r'[0-9a-f]{32}|[0-9]{15,17}')
        indexes = gwt_utils.find_docid_indexes(tokens, re_id)
        assert len(indexes) >= 10

    def test_extract_string_table_new_function(self, gwt_pages):
        """New extract_string_table function should work alongside extract_tokens."""
        _, raw = gwt_pages[0]
        st = gwt_utils.extract_string_table(raw)
        tokens = gwt_utils.extract_tokens(raw)
        # Both should return non-empty results
        assert st is not None and len(st) >= 20
        assert len(tokens) >= 50


# ===========================================================================
# 6. Edge case tests
# ===========================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_payload(self, spider):
        """Empty payload should return empty dict, not crash."""
        result = spider._extract_pdatum_from_string_table("")
        assert result == {}

    def test_garbage_payload(self, spider):
        """Invalid payload should return empty dict, not crash."""
        result = spider._extract_pdatum_from_string_table("not a GWT response")
        assert result == {}

    def test_minimal_ok_payload(self, spider):
        """Minimal //OK payload without enough data returns empty dict."""
        result = spider._extract_pdatum_from_string_table('//OK[0,[["class"],0]]')
        assert result == {}

    def test_st_with_no_docids(self, spider):
        """ST with no DocIds returns empty list."""
        st = ["hello", "world", "foo", "bar"] * 10  # 40 strings, no DocIds
        items = spider._st_extract_items(st)
        assert items == []
