"""
Module 5 – Unit Tests: RRF Scorer
"""

import uuid

import pytest

from app.utils.rrf import RRFScorer


def make_uuid(seed: str) -> uuid.UUID:
    """Deterministic UUID from a short seed string for readable test data."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed)


class TestRRFScorer:
    def test_empty_inputs_returns_empty(self):
        scorer = RRFScorer(k=60)
        result = scorer.combine([], [])
        assert result == {}

    def test_single_list_only_keyword(self):
        scorer = RRFScorer(k=60)
        p1, p2 = make_uuid("p1"), make_uuid("p2")
        keyword = [(p1, 0.9), (p2, 0.5)]
        result = scorer.combine(keyword, [])

        assert p1 in result
        assert p2 in result
        # p1 ranked #1 in keyword -> higher RRF score than p2 (#2)
        assert result[p1][0] > result[p2][0]
        assert result[p1][1] == "keyword"

    def test_single_list_only_semantic(self):
        scorer = RRFScorer(k=60)
        p1, p2 = make_uuid("p1"), make_uuid("p2")
        semantic = [(p1, 0.95), (p2, 0.6)]
        result = scorer.combine([], semantic)

        assert result[p1][1] == "semantic"
        assert result[p1][0] > result[p2][0]

    def test_paper_in_both_lists_scores_higher(self):
        """A paper ranked #1 in BOTH lists should score higher than a paper
        ranked #1 in only ONE list."""
        scorer = RRFScorer(k=60)
        p_both = make_uuid("both")
        p_keyword_only = make_uuid("keyword_only")

        keyword = [(p_both, 0.9), (p_keyword_only, 0.8)]
        semantic = [(p_both, 0.9)]

        result = scorer.combine(keyword, semantic)

        assert result[p_both][0] > result[p_keyword_only][0]

    def test_rrf_formula_correctness(self):
        """Verify the exact RRF math: 1/(k+rank) summed across lists."""
        scorer = RRFScorer(k=60)
        p1 = make_uuid("p1")

        # p1 is rank 1 in keyword (score irrelevant to RRF, only rank matters)
        keyword = [(p1, 0.99)]
        semantic = []

        result = scorer.combine(keyword, semantic)
        expected_score = 1.0 / (60 + 1)  # rank 1 -> 1/(60+1)
        assert abs(result[p1][0] - expected_score) < 1e-9

    def test_rank_sorts_descending_and_normalizes(self):
        scorer = RRFScorer(k=60)
        p1, p2, p3 = make_uuid("p1"), make_uuid("p2"), make_uuid("p3")

        keyword = [(p1, 0.9), (p2, 0.8), (p3, 0.1)]
        semantic = [(p1, 0.95)]

        rrf_scores = scorer.combine(keyword, semantic)
        ranked = scorer.rank(rrf_scores)

        # Sorted descending
        scores = [score for _, score, _ in ranked]
        assert scores == sorted(scores, reverse=True)

        # Top score normalized to 1.0
        assert abs(ranked[0][1] - 1.0) < 1e-9

        # p1 should be first (appears in both lists, rank 1 in both)
        assert ranked[0][0] == p1

    def test_different_k_changes_relative_weighting(self):
        """Higher k flattens the score distribution (less aggressive rank decay)."""
        p1, p2 = make_uuid("p1"), make_uuid("p2")
        keyword = [(p1, 0.9), (p2, 0.8)]

        scorer_low_k = RRFScorer(k=1)
        scorer_high_k = RRFScorer(k=1000)

        result_low = scorer_low_k.combine(keyword, [])
        result_high = scorer_high_k.combine(keyword, [])

        # With low k, rank 1 vs rank 2 difference is more pronounced (relatively)
        ratio_low = result_low[p1][0] / result_low[p2][0]
        ratio_high = result_high[p1][0] / result_high[p2][0]
        assert ratio_low > ratio_high

    def test_disjoint_lists_no_overlap(self):
        """Papers appearing in only one list still get scored and ranked correctly."""
        scorer = RRFScorer(k=60)
        p1, p2, p3, p4 = (
            make_uuid("p1"), make_uuid("p2"), make_uuid("p3"), make_uuid("p4")
        )
        keyword = [(p1, 0.9), (p2, 0.7)]
        semantic = [(p3, 0.95), (p4, 0.6)]

        result = scorer.combine(keyword, semantic)
        assert len(result) == 4
        assert all(pid in result for pid in (p1, p2, p3, p4))
