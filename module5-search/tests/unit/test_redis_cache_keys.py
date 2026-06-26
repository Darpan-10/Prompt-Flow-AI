"""
Module 5 – Unit Tests: Redis Cache Key Generation
"""

from app.services.redis_service import make_facets_cache_key, make_search_cache_key


class TestSearchCacheKey:
    def test_deterministic_for_identical_params(self):
        key1 = make_search_cache_key(
            query="attention", mode="hybrid", department_code="CSE",
            filters={"year": 2024}, limit=20, cursor=None,
        )
        key2 = make_search_cache_key(
            query="attention", mode="hybrid", department_code="CSE",
            filters={"year": 2024}, limit=20, cursor=None,
        )
        assert key1 == key2

    def test_different_query_different_key(self):
        key1 = make_search_cache_key("attention", "hybrid", "CSE", {}, 20, None)
        key2 = make_search_cache_key("transformer", "hybrid", "CSE", {}, 20, None)
        assert key1 != key2

    def test_different_department_different_key(self):
        """Critical for RLS correctness: same query in different departments
        must NEVER share a cache entry."""
        key1 = make_search_cache_key("attention", "hybrid", "CSE", {}, 20, None)
        key2 = make_search_cache_key("attention", "hybrid", "ECE", {}, 20, None)
        assert key1 != key2

    def test_different_mode_different_key(self):
        key1 = make_search_cache_key("attention", "keyword", "CSE", {}, 20, None)
        key2 = make_search_cache_key("attention", "semantic", "CSE", {}, 20, None)
        assert key1 != key2

    def test_filter_order_does_not_change_key(self):
        """Dict key order shouldn't matter -- orjson.OPT_SORT_KEYS ensures this."""
        key1 = make_search_cache_key(
            "test", "hybrid", "CSE", {"year": 2024, "paper_type": "journal"}, 20, None
        )
        key2 = make_search_cache_key(
            "test", "hybrid", "CSE", {"paper_type": "journal", "year": 2024}, 20, None
        )
        assert key1 == key2

    def test_key_has_search_prefix(self):
        key = make_search_cache_key("test", "hybrid", "CSE", {}, 20, None)
        assert key.startswith("search:")

    def test_different_cursor_different_key(self):
        key1 = make_search_cache_key("test", "hybrid", "CSE", {}, 20, cursor=None)
        key2 = make_search_cache_key("test", "hybrid", "CSE", {}, 20, cursor="abc123")
        assert key1 != key2


class TestFacetsCacheKey:
    def test_has_facets_prefix(self):
        key = make_facets_cache_key("CSE")
        assert key.startswith("facets:")

    def test_department_scoped(self):
        key1 = make_facets_cache_key("CSE")
        key2 = make_facets_cache_key("ECE")
        assert key1 != key2

    def test_deterministic(self):
        assert make_facets_cache_key("CSE") == make_facets_cache_key("CSE")
