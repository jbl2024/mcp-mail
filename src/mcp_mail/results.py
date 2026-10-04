"""Static JSON response contracts; TypedDict leaves wire dictionaries unchanged."""

from typing import Literal, NotRequired, TypedDict


class FolderInfo(TypedDict):
    """Discovered or allowlisted folder; missing folders are not selectable."""

    name: str
    selectable: bool


MessageSummary = TypedDict(
    "MessageSummary",
    {
        "uid": int,
        "message_id": str,
        "subject": str,
        "from": str,
        "to": str,
        "cc": str,
        "date": str,
        "references": list[str],
        "flags": list[str],
        "seen": bool,
        "flagged": bool,
        "important": bool,
        "size_bytes": int,
    },
)


class SearchMessage(MessageSummary):
    """Header summary with identity sufficient for subsequent message reads."""

    account: str
    folder: str
    uidvalidity: int


class FolderError(TypedDict):
    """Sanitized failure scoped to a folder, without server exception details."""

    folder: str
    error: str


class SearchResult(TypedDict):
    """Global page; top-level folder/UIDVALIDITY are null for a multi-folder search."""

    account: str
    folder: str | None
    uidvalidity: int | None
    folders_queried: list[str]
    total: int
    offset: int
    order: str
    messages: list[SearchMessage]
    next_offset: int | None
    partial: bool
    errors: NotRequired[list[FolderError]]


class AttachmentInfo(TypedDict):
    """MIME metadata; filename is untrusted display text, never a local path."""

    index: int
    filename: str | None
    content_type: str
    content_id: str
    size_bytes: int


class AttachmentResult(AttachmentInfo):
    """Extracted decoded payload represented as base64, without filesystem writes."""

    encoding: Literal["base64"]
    data: str


class BodyResult(TypedDict):
    """Converted body and explicit character-limit truncation status."""

    markdown: str
    body_truncated: bool


class MessageDetail(MessageSummary, BodyResult):
    """Full read response fields, combining headers, body and attachment indexes."""

    attachments: list[AttachmentInfo]


class MessageResult(TypedDict):
    """A message and the account/folder namespace under which it was read."""

    account: str
    folder: str
    uidvalidity: int | None
    message: MessageDetail


class ThreadResult(TypedDict):
    """Linked header summaries with separate scan and result truncation indicators."""

    account: str
    folder: str
    uidvalidity: int | None
    scope: str
    scan_truncated: bool
    truncated: bool
    messages: list[MessageSummary]


class AccountInfo(TypedDict):
    """Local account alias and optional restriction, without endpoint or credentials."""

    name: str
    label: str
    folders: list[str] | None


class AccountsResult(TypedDict):
    """Configured local accounts; this result needs no network request."""

    accounts: list[AccountInfo]


type ReadResult = list[FolderInfo] | SearchResult | MessageResult | AttachmentResult | ThreadResult


class ServiceResponse(TypedDict):
    """Trust envelope shared by all server-sourced read operations."""

    content_is_untrusted: Literal[True]
    trust_notice: str
    result: ReadResult
