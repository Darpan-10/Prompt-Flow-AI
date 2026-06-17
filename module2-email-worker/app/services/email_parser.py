"""
MIME Email Parser — extracts body text, thread headers, and attachments.
"""
import logging
from dataclasses import dataclass, field
from email.message import Message
from typing import List, Optional, Tuple
from email.header import decode_header
import quopri
import base64

logger = logging.getLogger(__name__)


@dataclass
class ParsedEmail:
    message_id: str
    in_reply_to: Optional[str]
    references: Optional[str]
    subject: str
    sender: str
    recipients: List[str]
    body_text: str
    attachments: List[Tuple[str, str, bytes]]  # (filename, content_type, raw_bytes)


def _decode_header_value(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded).strip()


def _extract_body(msg: Message) -> str:
    """Recursively extract plain text body from MIME message."""
    body_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if ct == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))

    return "\n".join(body_parts).strip()


def _extract_attachments(msg: Message) -> List[Tuple[str, str, bytes]]:
    """Extract all attachments. Returns (filename, content_type, raw_bytes)."""
    attachments = []

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition and "inline" not in disposition:
            continue

        filename = part.get_filename()
        if not filename:
            continue

        filename = _decode_header_value(filename)
        content_type = part.get_content_type()
        raw_bytes = part.get_payload(decode=True)

        if raw_bytes:
            attachments.append((filename, content_type, raw_bytes))
            logger.debug("Found attachment: %s (%s, %d bytes)", filename, content_type, len(raw_bytes))

    return attachments


def parse_email(msg: Message) -> ParsedEmail:
    """Parse a full email.Message into a structured ParsedEmail."""
    message_id = _decode_header_value(msg.get("Message-ID", "")).strip("<>")
    in_reply_to = _decode_header_value(msg.get("In-Reply-To", "")).strip("<>") or None
    references = msg.get("References", "").strip() or None
    subject = _decode_header_value(msg.get("Subject", "(no subject)"))
    sender = _decode_header_value(msg.get("From", ""))
    to_raw = msg.get("To", "")
    cc_raw = msg.get("Cc", "")
    recipients = [
        r.strip()
        for r in (to_raw + "," + cc_raw).split(",")
        if r.strip()
    ]

    body_text = _extract_body(msg)
    attachments = _extract_attachments(msg)

    return ParsedEmail(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        subject=subject,
        sender=sender,
        recipients=recipients,
        body_text=body_text,
        attachments=attachments,
    )
