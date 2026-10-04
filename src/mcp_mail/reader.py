"""Synchronous IMAP reads with independent sessions and no mailbox mutations."""

from __future__ import annotations

import ssl
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import date
from email.message import EmailMessage
from typing import Any, cast

from imapclient import IMAPClient

from .cache import MemoryCache
from .config import AccountConfig, CredentialProfile, Settings
from .models import (
    HEADER_FETCH,
    HEADER_KEY,
    attachments,
    body,
    extract_attachment,
    message_ids,
    parse_message,
    summary,
)
from .results import (
    AttachmentResult,
    FolderError,
    FolderInfo,
    MessageDetail,
    MessageResult,
    SearchMessage,
    SearchResult,
    ThreadResult,
)

# Folder and namespace travel with each UID so cross-folder pages cannot mix identities.
type SearchHit = tuple[str, int, int]


class MailReadError(ValueError):
    """Safe error returned to callers without server or credential details."""


class MailReader:
    """Read one configured account; each public operation owns its connection.

    No session is shared across threads. Read-only selection and PEEK protect
    normal reads; server-side account permissions remain the strongest boundary.
    """

    def __init__(
        self,
        account: AccountConfig,
        credentials: CredentialProfile,
        settings: Settings,
        client_factory: Callable[..., IMAPClient] = IMAPClient,
    ) -> None:
        """Bind configuration and an injectable connection factory for offline tests."""
        self.account, self.credentials, self.settings = account, credentials, settings
        self.client_factory = client_factory
        self._cache_username: str | None = None
        self._search_cache = MemoryCache(ttl=10, max_weight=50_000)
        self._body_cache = MemoryCache(ttl=30, max_weight=20 * 1024 * 1024)

    @contextmanager
    def session(
        self, folder: str | None = None, uidvalidity: int | None = None
    ) -> Iterator[tuple[IMAPClient, int | None]]:
        """Yield an authenticated TLS client and optional selected-folder UIDVALIDITY.

        Args:
            folder: Select via EXAMINE when provided; None leaves no folder selected.
            uidvalidity: Expected identifier namespace, checked before yielding.

        Raises:
            MailReadError: Folder validation, connection, authentication or namespace
                checks fail. Errors from the context body are also sanitized.

        Always attempts LOGOUT, including on failure. CLOSE is avoided because it
        can expunge messages. Cleanup errors cannot mask the original result.
        """
        if folder is not None and (
            not isinstance(folder, str) or not folder or any(c in folder for c in "\r\n\x00")
        ):
            raise MailReadError("Invalid folder name")
        if (
            folder is not None
            and self.account.folders is not None
            and folder not in self.account.folders
        ):
            raise MailReadError("Folder is outside the configured allowlist")
        client = None
        try:
            username, password = self.credentials.resolve()
            if self._cache_username is not None and username != self._cache_username:
                # A profile can resolve to another mailbox between operations.
                self._search_cache = MemoryCache(ttl=10, max_weight=50_000)
                self._body_cache = MemoryCache(ttl=30, max_weight=20 * 1024 * 1024)
            self._cache_username = username
            client = self.client_factory(
                self.account.host,
                port=self.account.port,
                use_uid=True,
                ssl=self.account.security == "tls",
                ssl_context=ssl.create_default_context(),
                timeout=self.settings.request_timeout_seconds,
            )
            if self.account.security == "starttls":
                client.starttls(ssl.create_default_context())
            client.login(username, password)
            selected = client.select_folder(folder, readonly=True) if folder else {}
            validity = selected.get(b"UIDVALIDITY")
            if folder and not isinstance(validity, int):
                raise MailReadError("Server did not return UIDVALIDITY")
            if uidvalidity is not None and validity != uidvalidity:
                raise MailReadError("UIDVALIDITY changed; search the folder again")
            yield client, validity
        except MailReadError:
            raise
        except Exception as exc:
            raise MailReadError(
                "IMAP read failed; check credentials, TLS and server availability"
            ) from exc
        finally:
            if client is not None:
                with suppress(Exception):
                    client.logout()

    def _folders(self, client: IMAPClient) -> list[FolderInfo]:
        """Discover names and selectability, retaining missing allowlisted names as unavailable."""
        listed = {name: flags for flags, _, name in client.list_folders()}
        names = self.account.folders if self.account.folders is not None else sorted(listed)
        return [
            {
                "name": name,
                "selectable": name in listed
                and not any(
                    (flag.decode() if isinstance(flag, bytes) else flag).lower() == "\\noselect"
                    for flag in listed[name]
                ),
            }
            for name in names
        ]

    def list_folders(self) -> list[FolderInfo]:
        """Return accessible folder descriptors without selecting a mailbox.

        Raises:
            MailReadError: Discovery or authentication fails.
        """
        with self.session() as (client, _):
            return self._folders(client)

    def search_messages(
        self,
        folder: str | None = None,
        query: str = "",
        sender: str = "",
        recipient: str = "",
        subject: str = "",
        seen: bool | None = None,
        flagged: bool | None = None,
        important: bool | None = None,
        since: str = "",
        before: str = "",
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "uid",
        sort_order: str = "desc",
    ) -> SearchResult:
        """Search on the server; date sorting scans all matching headers before pagination.

        Args:
            folder: A single folder, or None for every selectable accessible folder.
            query: IMAP TEXT criterion, matching headers and body.
            sender: FROM criterion; recipient uses TO, not CC or BCC.
            recipient: IMAP TO criterion.
            subject: IMAP SUBJECT criterion.
            seen: True for read, False for unread, None for either.
            flagged: Filter the Flagged flag, or None to leave it unrestricted.
            important: Filter Flagged OR $Important; None disables the filter.
            since: Inclusive internal-date lower bound, YYYY-MM-DD, or empty.
            before: Exclusive internal-date upper bound, YYYY-MM-DD, or empty.
            limit: Page size, between 1 and settings.max_results.
            offset: Non-negative offset across folders, not per folder.
            sort_by: uid (default), sent_at (Date header), or received_at (INTERNALDATE).
            sort_order: asc or desc. Unknown dates are always placed last.

        Returns:
            Headers with account/folder/UID/UIDVALIDITY identities, match count and
            next_offset. Default order is folder name then descending UID. Cross-folder
            failures set partial/errors; even an all-failed search returns partial.
            Disappearing messages can shorten a page. No snapshot or cross-folder
            deduplication is performed. UID matches may be up to 10 seconds old;
            cached matches do not cache flags or headers. All matches are held in memory.

        Raises:
            MailReadError: Validation, initial discovery or a single-folder operation
                fails. Unicode searches require the server to accept UTF-8.
        """
        if type(limit) is not int or not 1 <= limit <= self.settings.max_results:
            raise MailReadError("limit is outside the configured range")
        if type(offset) is not int or offset < 0:
            raise MailReadError("offset must be a non-negative integer")
        if sort_by not in ("uid", "sent_at", "received_at"):
            raise MailReadError("sort_by must be uid, sent_at or received_at")
        if sort_order not in ("asc", "desc"):
            raise MailReadError("sort_order must be asc or desc")
        criteria = self._search_criteria(
            query, sender, recipient, subject, seen, flagged, important, since, before
        )
        # A specific folder is validated by session(); the broad search discovers folders.
        with self.session(folder) as (client, validity):
            folders = (
                [folder]
                if folder is not None
                else sorted(f["name"] for f in self._folders(client) if f["selectable"])
            )
            matches, searched, errors = self._find_matches(
                client, folders, criteria, folder is not None, validity
            )
            if sort_by == "uid":
                if sort_order == "asc":
                    matches = sorted(matches, key=lambda hit: (hit[0], hit[2]))
                messages, read_errors = self._fetch_page(
                    client, matches[offset : offset + limit], folder is not None
                )
                errors.extend(read_errors)
                sort_complete = not errors
                page_total = len(matches)
            else:
                # Portable global sorting: scan headers only, in bounded requests.
                all_messages = []
                for start in range(0, len(matches), 100):
                    batch, read_errors = self._fetch_page(
                        client, matches[start : start + 100], folder is not None
                    )
                    all_messages.extend(batch)
                    errors.extend(read_errors)
                dated = [m for m in all_messages if m[sort_by] is not None]
                undated = [m for m in all_messages if m[sort_by] is None]
                dated.sort(
                    key=lambda m: (m[sort_by], m["folder"], m["uid"]), reverse=sort_order == "desc"
                )
                ordered = dated + undated
                sort_complete = not errors and not undated and len(all_messages) == len(matches)
                page_total = len(ordered)
                messages = ordered[offset : offset + limit]
            result: SearchResult = {
                "account": self.account.name,
                "folder": folder,
                "uidvalidity": validity,
                "folders_queried": searched,
                "total": len(matches),
                "offset": offset,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "sort_complete": sort_complete,
                "order": (
                    f"folder name ascending, then UID {sort_order}ending"
                    if sort_by == "uid"
                    else f"{sort_by} {sort_order}ending across folders; unknown dates last"
                ),
                "messages": messages,
                "next_offset": offset + limit if offset + limit < page_total else None,
                "partial": bool(errors) or (sort_by != "uid" and not sort_complete),
            }
            if errors:
                result["errors"] = errors
            return result

    @staticmethod
    def _search_criteria(
        query: str,
        sender: str,
        recipient: str,
        subject: str,
        seen: bool | None,
        flagged: bool | None,
        important: bool | None,
        since: str,
        before: str,
    ) -> list[Any]:
        """Validate filters and build IMAPClient criteria before any network access."""
        criteria: list[Any] = ["NOT", "DELETED"]
        for key, text in [
            ("TEXT", query),
            ("FROM", sender),
            ("TO", recipient),
            ("SUBJECT", subject),
        ]:
            if not isinstance(text, str) or len(text) > 1000 or any(c in text for c in "\r\n\x00"):
                raise MailReadError("Invalid search text")
            if text:
                criteria.extend([key, text])
        for key, flag in [("SEEN", seen), ("FLAGGED", flagged)]:
            if flag is not None:
                if type(flag) is not bool:
                    raise MailReadError("Flag filters must be booleans")
                criteria.extend([key] if flag else ["UN" + key])
        if important is not None:
            if type(important) is not bool:
                raise MailReadError("important must be a boolean")
            expression = ["OR", "FLAGGED", ["KEYWORD", "$Important"]]
            criteria.append(expression if important else ["NOT", expression])
        dates: dict[str, date] = {}
        for key, date_text in [("SINCE", since), ("BEFORE", before)]:
            if date_text:
                try:
                    dates[key] = date.fromisoformat(date_text)
                except (TypeError, ValueError) as exc:
                    raise MailReadError("Dates must use YYYY-MM-DD") from exc
                criteria.extend([key, dates[key]])
        if len(dates) == 2 and dates["SINCE"] >= dates["BEFORE"]:
            raise MailReadError("before must be after since")
        return criteria

    def _find_matches(
        self,
        client: IMAPClient,
        folders: list[str],
        criteria: list[Any],
        single_folder: bool,
        validity: int | None,
    ) -> tuple[list[SearchHit], list[str], list[FolderError]]:
        """Collect cached or fresh UID matches, preserving per-folder partial failures."""
        matches: list[SearchHit] = []
        searched: list[str] = []
        errors: list[FolderError] = []
        for name in folders:
            try:
                current = cast(int, validity)
                if not single_folder:
                    current = client.select_folder(name, readonly=True).get(b"UIDVALIDITY")
                    if type(current) is not int or current < 1:
                        raise MailReadError("Server did not return UIDVALIDITY")
                cache_key = (name, current, repr(criteria))
                uids = self._search_cache.get(cache_key)
                if uids is None:
                    uids = tuple(sorted(client.search(criteria, charset="UTF-8"), reverse=True))
                    self._search_cache.put(cache_key, uids, len(uids))
                matches.extend((name, current, uid) for uid in uids)
                searched.append(name)
            except Exception as exc:
                if single_folder:
                    raise MailReadError("IMAP folder search failed") from exc
                errors.append({"folder": name, "error": "IMAP folder search failed"})
        return matches, searched, errors

    def _fetch_page(
        self,
        client: IMAPClient,
        page: list[SearchHit],
        single_folder: bool,
    ) -> tuple[list[SearchMessage], list[FolderError]]:
        """Fetch only paged headers, rechecking namespaces after switching folders."""
        messages: list[SearchMessage] = []
        errors: list[FolderError] = []
        for name in dict.fromkeys(hit[0] for hit in page):
            hits = [hit for hit in page if hit[0] == name]
            current = hits[0][1]
            try:
                if not single_folder:
                    selected = client.select_folder(name, readonly=True)
                    if selected.get(b"UIDVALIDITY") != current:
                        raise MailReadError("UIDVALIDITY changed; search again")
                data = client.fetch(
                    [hit[2] for hit in hits], [HEADER_FETCH, "FLAGS", "RFC822.SIZE", "INTERNALDATE"]
                )
                for _, _, uid in hits:
                    if uid in data and HEADER_KEY in data[uid]:
                        hit: SearchMessage = {
                            **summary(
                                parse_message(data[uid][HEADER_KEY]),
                                uid,
                                data[uid].get(b"FLAGS", ()),
                                data[uid].get(b"RFC822.SIZE", 0),
                                data[uid].get(b"INTERNALDATE"),
                            ),
                            "account": self.account.name,
                            "folder": name,
                            "uidvalidity": current,
                        }
                        messages.append(hit)
            except Exception as exc:
                if single_folder:
                    raise MailReadError("IMAP header read failed") from exc
                errors.append({"folder": name, "error": "IMAP header read failed; search again"})
        return messages, errors

    def _message(
        self, client: IMAPClient, uid: int, folder: str, uidvalidity: int
    ) -> tuple[EmailMessage, dict[bytes, Any]]:
        """Fetch a selected-folder message via PEEK after checking its advertised size.

        Recheck actual received size to reject incorrect server metadata. A message
        may disappear between metadata and content fetches; that is a read error.
        Content can be reused for 30 seconds, scoped by folder, UIDVALIDITY, UID
        and size. Existence and flags are fetched anew, including on cache hits.
        """
        if type(uid) is not int or not 1 <= uid <= 4294967295:
            raise MailReadError("Invalid UID")
        metadata = client.fetch([uid], ["RFC822.SIZE", "FLAGS", "INTERNALDATE"]).get(uid, {})
        size = metadata.get(b"RFC822.SIZE")
        if size is None:
            raise MailReadError("Message not found")
        if size > self.settings.max_message_bytes:
            raise MailReadError("Message exceeds configured size limit")
        cache_key = (folder, uidvalidity, uid, size)
        raw = self._body_cache.get(cache_key)
        if raw is None:
            data = client.fetch([uid], ["BODY.PEEK[]"]).get(uid, {})
            raw = data.get(b"BODY[]")
        if raw is None:
            raise MailReadError("Message no longer available")
        if len(raw) > self.settings.max_message_bytes:
            raise MailReadError("Message exceeds configured size limit")
        if self._body_cache.get(cache_key) is None:
            self._body_cache.put(cache_key, raw, len(raw))
        return parse_message(raw), metadata

    def get_message(self, folder: str, uid: int, uidvalidity: int) -> MessageResult:
        """Return message headers, bounded Markdown and attachment descriptors.

        ``folder``, ``uid`` and ``uidvalidity`` must come from the same search hit.
        MailService requires a positive UIDVALIDITY before calling this method.

        Raises:
            MailReadError: Identity is stale, message is missing/oversized, or reading fails.
        """
        with self.session(folder, uidvalidity) as (client, validity):
            message, meta = self._message(client, uid, folder, uidvalidity)
            detail: MessageDetail = {
                **summary(
                    message,
                    uid,
                    meta.get(b"FLAGS", ()),
                    meta[b"RFC822.SIZE"],
                    meta.get(b"INTERNALDATE"),
                ),
                **body(message, self.settings.max_text_length),
                "attachments": attachments(message),
            }
            return {
                "account": self.account.name,
                "folder": folder,
                "uidvalidity": validity,
                "message": detail,
            }

    def get_attachment(
        self, folder: str, uid: int, uidvalidity: int, index: int
    ) -> AttachmentResult:
        """Return base64 attachment data for an index previously listed by get_message.

        The complete MIME message is fetched under max_message_bytes before the
        decoded attachment is checked against max_attachment_bytes. No file is written.

        Raises:
            MailReadError: Identity, index, size limits or IMAP reads fail.
        """
        with self.session(folder, uidvalidity) as (client, _):
            message, _ = self._message(client, uid, folder, uidvalidity)
            try:
                return extract_attachment(message, index, self.settings.max_attachment_bytes)
            except ValueError as exc:
                raise MailReadError(str(exc)) from exc

    def get_thread(self, folder: str, uid: int, uidvalidity: int, limit: int = 50) -> ThreadResult:
        """Return linked thread headers within a bounded scan of one folder.

        The seed is always included in the scan; recent non-deleted UIDs are
        limited by max_thread_messages. Shared Message-ID/References/In-Reply-To
        identifiers form a transitive connection; subjects are not considered.
        Results use ascending UID, not sender-provided Date headers.

        Returns:
            Header summaries, scan_truncated for incomplete folder coverage, and
            truncated when the linked messages exceed the requested result limit.

        Raises:
            MailReadError: Invalid limit, stale seed identity, oversize seed or read failure.
        """
        if type(limit) is not int or not 1 <= limit <= self.settings.max_results:
            raise MailReadError("Invalid thread limit")
        with self.session(folder, uidvalidity) as (client, validity):
            seed, _ = self._message(client, uid, folder, uidvalidity)
            all_uids = sorted(client.search(["NOT", "DELETED"]), reverse=True)
            scanned = all_uids[: self.settings.max_thread_messages]
            if uid not in scanned:
                scanned.append(uid)
            headers = {}
            for start in range(0, len(scanned), 100):
                headers.update(
                    client.fetch(
                        scanned[start : start + 100],
                        [HEADER_FETCH, "FLAGS", "RFC822.SIZE", "INTERNALDATE"],
                    )
                )
            parsed = {
                key: parse_message(value[HEADER_KEY])
                for key, value in headers.items()
                if HEADER_KEY in value and key in scanned
            }
            identifiers = set(message_ids(seed))
            found = {uid}
            changed = True
            while changed:
                changed = False
                for key, message in parsed.items():
                    ids = set(message_ids(message))
                    if key not in found and identifiers & ids:
                        found.add(key)
                        identifiers.update(ids)
                        changed = True
            ordered = sorted(found)
            return {
                "account": self.account.name,
                "folder": folder,
                "uidvalidity": validity,
                "scope": "same folder; Message-ID/References/In-Reply-To links",
                "scan_truncated": len(all_uids) > self.settings.max_thread_messages,
                "truncated": len(ordered) > limit,
                "messages": [
                    summary(
                        parsed[key],
                        key,
                        headers[key].get(b"FLAGS", ()),
                        headers[key].get(b"RFC822.SIZE", 0),
                        headers[key].get(b"INTERNALDATE"),
                    )
                    for key in ordered[:limit]
                    if key in parsed
                ],
            }
