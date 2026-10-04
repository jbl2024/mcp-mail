"""MIME parsing and JSON-ready representations of untrusted mail content."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from markdownify import markdownify

from .results import AttachmentInfo, AttachmentResult, BodyResult, MessageSummary

HEADER_FIELDS = "MESSAGE-ID REFERENCES IN-REPLY-TO SUBJECT FROM TO CC DATE"
HEADER_FETCH = f"BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})]"

# PEEK is a request modifier; IMAP response keys omit it.
HEADER_KEY = f"BODY[HEADER.FIELDS ({HEADER_FIELDS})]".encode()


def parse_message(raw: bytes) -> EmailMessage:
    """Parse RFC 5322 bytes using the modern email policy, including header decoding.

    This does not authenticate senders or validate content. Tolerated MIME defects
    remain attached to the email object; callers must bound input size first.
    """
    return BytesParser(policy=policy.default).parsebytes(raw)


def message_ids(message: EmailMessage) -> list[str]:
    """Collect bracketed IDs from identity and reply headers, preserving duplicates.

    This is a threading heuristic, not validation of global identifier uniqueness.
    """
    return re.findall(
        r"<[^<>\s]+>",
        str(message.get("Message-ID", ""))
        + " "
        + str(message.get("References", ""))
        + " "
        + str(message.get("In-Reply-To", "")),
    )


def normalized_date(value: object) -> str | None:
    """Return an aware UTC timestamp, or None for invalid/unknown-zone dates."""
    try:
        parsed = value if isinstance(value, datetime) else parsedate_to_datetime(str(value))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


def summary(
    message: EmailMessage,
    uid: int,
    flags: Iterable[bytes | str],
    size: int,
    received_at: object = None,
) -> MessageSummary:
    """Return decoded headers and flag indicators without downloading a message body.

    ``size`` is the server-reported RFC822.SIZE. ``important`` reflects Flagged or
    $Important, not an assessment of content. The caller supplies folder identity
    separately because a UID alone is insufficient to identify a message.
    """
    flags = [f.decode(errors="replace") if isinstance(f, bytes) else str(f) for f in flags]
    return {
        "uid": uid,
        "message_id": str(message.get("Message-ID", "")),
        "subject": str(message.get("Subject", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "cc": str(message.get("Cc", "")),
        "date": str(message.get("Date", "")),
        "sent_at": normalized_date(message.get("Date", "")),
        "received_at": normalized_date(received_at),
        "references": message_ids(message),
        "flags": flags,
        "seen": "\\Seen" in flags,
        "flagged": "\\Flagged" in flags,
        "important": "\\Flagged" in flags or "$Important" in flags,
        "size_bytes": size,
    }


def attachment_parts(message: EmailMessage) -> list[EmailMessage]:
    """Find attachments and non-text CID resources in MIME traversal order.

    Attached messages and multipart attachments are returned as a single part;
    their descendants do not become additional independently indexed attachments.
    """
    result = []

    def visit(part: EmailMessage) -> None:
        """Stop traversal at attachment boundaries to keep indexes unambiguous."""
        if (
            part.get_filename() is not None
            or part.get_content_disposition() == "attachment"
            or (part.get_content_maintype() != "text" and part.get("Content-ID"))
        ):
            result.append(part)
        elif part.is_multipart():
            for child in part.iter_parts():
                visit(child)

    visit(message)
    return result


def attachment_bytes(part: EmailMessage) -> bytes:
    """Decode transfer encoding, or serialize children of an attached MIME container.

    Attached message bytes are reserialized with SMTP line endings and need not
    match the original wire bytes. This function performs no filesystem access.
    """
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    children = part.get_payload()
    if isinstance(children, list):
        return b"\r\n".join(child.as_bytes(policy=policy.SMTP) for child in children)
    return b""


def attachments(message: EmailMessage) -> list[AttachmentInfo]:
    """Return zero-based indexes and decoded sizes for the same parts used by extraction.

    Filenames are untrusted display metadata and must never be used as paths
    without validation by a consuming application.
    """
    return [
        {
            "index": i,
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "content_id": str(part.get("Content-ID", "")),
            "size_bytes": len(attachment_bytes(part)),
        }
        for i, part in enumerate(attachment_parts(message))
    ]


def body(message: EmailMessage, max_length: int) -> BodyResult:
    """Return a preferred body as Markdown and a character-truncation indicator.

    Plain text is preferred over HTML. Invalid charset bytes are replaced and
    unknown charset names fall back to UTF-8. HTML scripts, styles and images
    are removed without fetching external resources. Links remain untrusted;
    this is text conversion, not a sanitizer for subsequent HTML rendering.
    """
    part = message.get_body(preferencelist=("plain", "html"))
    text = ""
    if part is not None and part not in attachment_parts(message):
        payload = part.get_payload(decode=True) or b""
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            soup = BeautifulSoup(text, "html.parser")
            for element in soup(["script", "style", "img"]):
                element.decompose()
            text = markdownify(str(soup), heading_style="ATX")
    return {"markdown": text[:max_length], "body_truncated": len(text) > max_length}


def extract_attachment(message: EmailMessage, index: int, max_bytes: int) -> AttachmentResult:
    """Return attachment metadata and base64 content without writing a file.

    Args:
        message: Parsed, size-bounded MIME message.
        index: Zero-based index from attachments(), scoped to this message.
        max_bytes: Maximum decoded payload size, before base64 expansion.

    Raises:
        ValueError: The index is invalid or the decoded attachment exceeds the limit.
    """
    parts = attachment_parts(message)
    if type(index) is not int or not 0 <= index < len(parts):
        raise ValueError("Unknown attachment index")
    payload = attachment_bytes(parts[index])
    if len(payload) > max_bytes:
        raise ValueError("Attachment exceeds configured size limit")
    return {
        **attachments(message)[index],
        "encoding": "base64",
        "data": base64.b64encode(payload).decode("ascii"),
    }
