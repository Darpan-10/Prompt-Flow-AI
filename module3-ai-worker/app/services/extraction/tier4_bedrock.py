"""
Tier 4: AWS Bedrock LLM Fallback.

COST CONTROLS (from locked spec):
  - ONLY invoked if cumulative confidence < 0.70
  - Raw text TRUNCATED to 1,500 tokens (~6,000 chars) — first ~2 pages only
  - Model: anthropic.claude-3-haiku-20240307-v1:0 (cheapest/fastest)
  - Confidence hard-capped at 0.90
  - Strict JSON output — no markdown wrappers
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Max ~1,500 tokens ≈ 6,000 characters (conservative estimate)
_MAX_TEXT_CHARS = 6000

_SYSTEM_PROMPT = """You are an academic paper metadata extractor.
Extract metadata from the provided paper text.
Output ONLY raw JSON. No markdown. No ```json wrappers. No explanation.
The JSON must have exactly these keys:
{
  "title": "string or null",
  "authors": ["list of full author names"],
  "year": integer or null,
  "venue": "journal or conference name or null",
  "abstract": "first 200 chars of abstract or null",
  "doi": "DOI string or null"
}
If a field cannot be determined with certainty, use null."""


@dataclass
class BedrockResult:
    title: Optional[str]        = None
    authors: List[str]          = field(default_factory=list)
    year: Optional[int]         = None
    venue: Optional[str]        = None
    abstract: Optional[str]     = None
    doi: Optional[str]          = None
    confidence: float           = 0.0
    invoked: bool               = False
    error: Optional[str]        = None


def _truncate_to_tokens(text: str) -> str:
    """Truncate text to ~1,500 tokens (first 6,000 chars = ~2 pages)."""
    return text[:_MAX_TEXT_CHARS]


def _parse_bedrock_response(response_text: str) -> dict:
    """
    Parse strict JSON from Bedrock response.
    Strips any accidental markdown wrapping.
    """
    text = response_text.strip()
    # Strip markdown wrappers if present despite instructions
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return json.loads(text)


def extract_with_bedrock(raw_text: str, current_confidence: float) -> BedrockResult:
    """
    Tier 4: AWS Bedrock LLM extraction.

    GATE: Only invokes if current_confidence < settings.llm_confidence_threshold (0.70).
    COST: Truncates input to ~1,500 tokens.
    CAP: Confidence hard-capped at settings.llm_confidence_cap (0.90).
    """
    # ── Strict fallback gate ──────────────────────────────────────────────
    if current_confidence >= settings.llm_confidence_threshold:
        logger.debug(
            "Bedrock skipped — confidence %.3f >= threshold %.3f",
            current_confidence, settings.llm_confidence_threshold,
        )
        return BedrockResult(invoked=False)

    logger.info(
        "Bedrock invoked — confidence %.3f < threshold %.3f",
        current_confidence, settings.llm_confidence_threshold,
    )

    truncated_text = _truncate_to_tokens(raw_text)

    client = boto3.client(
        service_name="bedrock-runtime",
        region_name=settings.aws_region,
    )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": settings.bedrock_max_tokens,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Extract metadata from this academic paper:\n\n{truncated_text}",
            }
        ],
    }

    try:
        response = client.invoke_model(
            modelId=settings.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        content_blocks = response_body.get("content", [])
        if not content_blocks:
            raise ValueError("Empty content in Bedrock response")

        response_text = content_blocks[0].get("text", "")
        parsed = _parse_bedrock_response(response_text)

        # Extract and validate fields
        title    = parsed.get("title")
        authors  = parsed.get("authors", [])
        year_raw = parsed.get("year")
        venue    = parsed.get("venue")
        abstract = parsed.get("abstract")
        doi      = parsed.get("doi")

        # Type safety
        if not isinstance(authors, list):
            authors = []
        year = int(year_raw) if year_raw and str(year_raw).isdigit() else None

        # Compute raw confidence based on fields found
        fields_found = sum([
            bool(title),
            bool(authors),
            bool(year),
            bool(venue),
        ])
        raw_confidence = 0.70 + (fields_found / 4) * 0.20  # 0.70 → 0.90

        # HARD CAP at 0.90
        final_confidence = min(raw_confidence, settings.llm_confidence_cap)

        logger.info(
            "Bedrock result — title=%s | authors=%d | year=%s | confidence=%.3f (capped at %.2f)",
            bool(title), len(authors), year, final_confidence, settings.llm_confidence_cap,
        )

        return BedrockResult(
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            abstract=abstract,
            doi=doi,
            confidence=final_confidence,
            invoked=True,
        )

    except ClientError as e:
        error_msg = f"Bedrock ClientError: {e.response['Error']['Message']}"
        logger.error(error_msg)
        return BedrockResult(invoked=True, error=error_msg)
    except json.JSONDecodeError as e:
        error_msg = f"Bedrock JSON parse error: {str(e)}"
        logger.error(error_msg)
        return BedrockResult(invoked=True, error=error_msg)
    except Exception as e:
        error_msg = f"Bedrock unexpected error: {str(e)}"
        logger.error(error_msg)
        return BedrockResult(invoked=True, error=error_msg)
