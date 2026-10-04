from __future__ import annotations

import base64
import re
from email import policy
from email.parser import BytesParser

from bs4 import BeautifulSoup
from markdownify import markdownify

HEADER_FIELDS = "MESSAGE-ID REFERENCES IN-REPLY-TO SUBJECT FROM TO CC DATE"
HEADER_FETCH = f"BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})]"
HEADER_KEY = f"BODY[HEADER.FIELDS ({HEADER_FIELDS})]".encode()


def parse_message(raw: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw)


def message_ids(message) -> list[str]:
    return re.findall(
        r"<[^<>\s]+>",
        str(message.get("Message-ID", ""))
        + " "
        + str(message.get("References", ""))
        + " "
        + str(message.get("In-Reply-To", "")),
    )


def summary(message, uid, flags, size):
    flags = [f.decode(errors="replace") if isinstance(f, bytes) else str(f) for f in flags]
    return {
        "uid": uid,
        "message_id": str(message.get("Message-ID", "")),
        "subject": str(message.get("Subject", "")),
        "from": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "cc": str(message.get("Cc", "")),
        "date": str(message.get("Date", "")),
        "references": message_ids(message),
        "flags": flags,
        "seen": "\\Seen" in flags,
        "flagged": "\\Flagged" in flags,
        "important": "\\Flagged" in flags or "$Important" in flags,
        "size_bytes": size,
    }


def attachment_parts(message):
    result = []

    def visit(part):
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


def attachment_bytes(part):
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    children = part.get_payload()
    if isinstance(children, list):
        return b"\r\n".join(child.as_bytes(policy=policy.SMTP) for child in children)
    return b""


def attachments(message):
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


def body(message, max_length):
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


def extract_attachment(message, index, max_bytes):
    parts = attachment_parts(message)
    if type(index) is not int or not 0 <= index < len(parts):
        raise ValueError("Unknown attachment index")
    payload = attachment_bytes(parts[index])
    if len(payload) > max_bytes:
        raise ValueError("Attachment exceeds configured size limit")
    return attachments(message)[index] | {
        "encoding": "base64",
        "data": base64.b64encode(payload).decode("ascii"),
    }
