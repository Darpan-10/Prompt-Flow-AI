"""
Deterministic Routing Engine.

Rules (locked spec, evaluated in order):
  1. BLOCK   if faculty_status in ['not_found', 'inactive']
  2. BLOCK   if any validation_issue has action='BLOCK'
  3. REVIEW  if overall_confidence < confidence_threshold
  4. REVIEW  if any validation_issue has action='REVIEW_REQUIRED'
  5. AUTO_SAVE otherwise

Output maps to Kafka topic:
  AUTO_SAVE       → papers.validated
  REVIEW_REQUIRED → papers.review
  BLOCK           → papers.failed
"""
import logging
from typing import List

from app.config import settings
from app.models.schemas import (
    RoutingDecision,
    RoutingDecisionBlock,
    ValidationIssue,
    EnrichedContext,
    OverallConfidence,
    FacultyStatus,
)

logger = logging.getLogger(__name__)

_TOPIC_MAP = {
    RoutingDecision.AUTO_SAVE:       settings.kafka_topic_papers_validated,
    RoutingDecision.REVIEW_REQUIRED: settings.kafka_topic_papers_review,
    RoutingDecision.BLOCK:           settings.kafka_topic_papers_failed,
}


def compute_routing(
    enriched_context: EnrichedContext,
    overall_confidence: OverallConfidence,
    validation_issues: List[ValidationIssue],
    confidence_threshold: float = None,
) -> RoutingDecisionBlock:
    """
    Deterministically compute routing decision.
    Evaluates ALL rules and collects reasons before returning.
    """
    threshold = confidence_threshold or settings.default_confidence_threshold
    reasons: List[str] = []
    decision = RoutingDecision.AUTO_SAVE  # default — upgraded as rules fire

    # ── Rule 1: Faculty status BLOCK ─────────────────────────────────────
    if enriched_context.faculty_status in (
        FacultyStatus.not_found, FacultyStatus.inactive
    ):
        decision = RoutingDecision.BLOCK
        reasons.append(
            f"faculty_status='{enriched_context.faculty_status.value}' "
            f"for faculty_id='{enriched_context.faculty_id}'"
        )

    # ── Rule 2: Validation issue BLOCK ───────────────────────────────────
    for issue in validation_issues:
        if issue.action == "BLOCK":
            decision = RoutingDecision.BLOCK
            reasons.append(f"validation_issue BLOCK: [{issue.code}] {issue.message}")

    # ── Rule 3: Low confidence → REVIEW (only if not already BLOCK) ───────
    if decision != RoutingDecision.BLOCK:
        if overall_confidence.score < threshold:
            decision = RoutingDecision.REVIEW_REQUIRED
            reasons.append(
                f"overall_confidence={overall_confidence.score:.4f} "
                f"< threshold={threshold:.4f}"
            )

    # ── Rule 4: Validation issue REVIEW (only if not already BLOCK) ───────
    if decision != RoutingDecision.BLOCK:
        for issue in validation_issues:
            if issue.action == "REVIEW_REQUIRED":
                decision = RoutingDecision.REVIEW_REQUIRED
                reasons.append(
                    f"validation_issue REVIEW_REQUIRED: [{issue.code}] {issue.message}"
                )

    # ── Rule 5: AUTO_SAVE (no reasons means all clear) ────────────────────
    if not reasons:
        reasons.append(
            f"all checks passed | confidence={overall_confidence.score:.4f} "
            f">= threshold={threshold:.4f} | faculty_status=active"
        )

    target_topic = _TOPIC_MAP[decision]

    logger.info(
        "Routing decision: %s → %s | reasons: %s",
        decision.value, target_topic, "; ".join(reasons),
    )

    return RoutingDecisionBlock(
        final_action=decision,
        reasons=reasons,
        target_topic=target_topic,
        confidence_threshold_used=threshold,
        overall_confidence=overall_confidence.score,
    )
