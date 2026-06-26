"""
Module 5 – Reciprocal Rank Fusion (RRF)
Combines keyword and semantic search results using RRF formula.

RRF Score = sum(1/(k + rank_keyword) + 1/(k + rank_semantic))
Where k = 60 (standard constant)

RRF is robust to score scale differences. It only cares about relative ranking,
making it stable across diverse queries (keyword ts_rank vs semantic cosine similarity).
"""

from __future__ import annotations

from typing import Dict, List, Tuple
from uuid import UUID


class RRFScorer:
    """Combines two ranked result sets using Reciprocal Rank Fusion."""

    def __init__(self, k: int = 60):
        """
        Initialize RRF scorer.
        
        k: RRF constant (default 60). Higher k = more weight to both rankings.
        """
        self.k = k

    def combine(
        self,
        keyword_results: List[Tuple[UUID, float]],  # [(paper_id, ts_rank_score), ...]
        semantic_results: List[Tuple[UUID, float]],  # [(paper_id, cosine_similarity), ...]
    ) -> Dict[UUID, Tuple[float, str]]:
        """
        Combine two ranked lists using RRF.
        
        Returns:
            Dict mapping paper_id -> (rrf_score, dominant_mode)
            where dominant_mode is "keyword" or "semantic" (whichever ranked it higher)
        """
        # Build rank mappings
        keyword_ranks = {paper_id: (idx + 1, score) for idx, (paper_id, score) in enumerate(keyword_results)}
        semantic_ranks = {paper_id: (idx + 1, score) for idx, (paper_id, score) in enumerate(semantic_results)}

        # Collect all paper IDs that appear in at least one result set
        all_paper_ids = set(keyword_ranks.keys()) | set(semantic_ranks.keys())

        # Calculate RRF scores
        rrf_scores: Dict[UUID, Tuple[float, str]] = {}
        for paper_id in all_paper_ids:
            keyword_rank, keyword_score = keyword_ranks.get(paper_id, (float('inf'), 0.0))
            semantic_rank, semantic_score = semantic_ranks.get(paper_id, (float('inf'), 0.0))

            # RRF formula
            rrf_score = 0.0
            if keyword_rank != float('inf'):
                rrf_score += 1.0 / (self.k + keyword_rank)
            if semantic_rank != float('inf'):
                rrf_score += 1.0 / (self.k + semantic_rank)

            # Determine dominant mode (which ranking had it higher)
            dominant_mode = "keyword" if keyword_rank < semantic_rank else "semantic"

            rrf_scores[paper_id] = (rrf_score, dominant_mode)

        return rrf_scores

    def rank(self, rrf_scores: Dict[UUID, Tuple[float, str]]) -> List[Tuple[UUID, float, str]]:
        """
        Sort RRF scores in descending order and normalize to [0, 1].
        
        Returns:
            List of (paper_id, normalized_score, dominant_mode) sorted by score DESC
        """
        if not rrf_scores:
            return []

        # Find max score for normalization
        max_score = max(score for score, _ in rrf_scores.values())

        # Sort and normalize
        sorted_results = sorted(
            ((pid, score, mode) for pid, (score, mode) in rrf_scores.items()),
            key=lambda x: x[1],
            reverse=True,
        )

        # Normalize to [0, 1]
        normalized = [
            (pid, score / max_score if max_score > 0 else 0.0, mode)
            for pid, score, mode in sorted_results
        ]

        return normalized
