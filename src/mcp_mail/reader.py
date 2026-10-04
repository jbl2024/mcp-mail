from __future__ import annotations

import ssl
from contextlib import contextmanager, suppress
from datetime import date

from imapclient import IMAPClient

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


class MailReadError(ValueError):
    """Safe error returned to callers without server or credential details."""


class MailReader:
    def __init__(self, account, credentials, settings, client_factory=IMAPClient):
        self.account, self.credentials, self.settings = account, credentials, settings
        self.client_factory = client_factory

    @contextmanager
    def session(self, folder=None, uidvalidity=None):
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

    def _folders(self, client):
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

    def list_folders(self):
        with self.session() as (client, _):
            return self._folders(client)

    def search_messages(
        self,
        folder=None,
        query="",
        sender="",
        recipient="",
        subject="",
        seen=None,
        flagged=None,
        important=None,
        since="",
        before="",
        limit=50,
        offset=0,
    ):
        if type(limit) is not int or not 1 <= limit <= self.settings.max_results:
            raise MailReadError("limit is outside the configured range")
        if type(offset) is not int or offset < 0:
            raise MailReadError("offset must be a non-negative integer")
        criteria = ["NOT", "DELETED"]
        for key, value in [
            ("TEXT", query),
            ("FROM", sender),
            ("TO", recipient),
            ("SUBJECT", subject),
        ]:
            if (
                not isinstance(value, str)
                or len(value) > 1000
                or any(c in value for c in "\r\n\x00")
            ):
                raise MailReadError("Invalid search text")
            if value:
                criteria.extend([key, value])
        for key, value in [("SEEN", seen), ("FLAGGED", flagged)]:
            if value is not None:
                if type(value) is not bool:
                    raise MailReadError("Flag filters must be booleans")
                criteria.extend([key] if value else ["UN" + key])
        if important is not None:
            if type(important) is not bool:
                raise MailReadError("important must be a boolean")
            expression = ["OR", "FLAGGED", ["KEYWORD", "$Important"]]
            criteria.append(expression if important else ["NOT", expression])
        dates = {}
        for key, value in [("SINCE", since), ("BEFORE", before)]:
            if value:
                try:
                    dates[key] = date.fromisoformat(value)
                except (TypeError, ValueError) as exc:
                    raise MailReadError("Dates must use YYYY-MM-DD") from exc
                criteria.extend([key, dates[key]])
        if len(dates) == 2 and dates["SINCE"] >= dates["BEFORE"]:
            raise MailReadError("before must be after since")
        # A specific folder is validated by session(); the broad search discovers folders.
        with self.session(folder) as (client, validity):
            folders = (
                [folder]
                if folder is not None
                else sorted(f["name"] for f in self._folders(client) if f["selectable"])
            )
            matches = []
            errors = []
            searched = []
            for name in folders:
                try:
                    current = validity
                    if folder is None:
                        current = client.select_folder(name, readonly=True).get(b"UIDVALIDITY")
                        if type(current) is not int or current < 1:
                            raise MailReadError("Server did not return UIDVALIDITY")
                    uids = sorted(client.search(criteria, charset="UTF-8"), reverse=True)
                    matches.extend((name, current, uid) for uid in uids)
                    searched.append(name)
                except Exception as exc:
                    if folder is not None:
                        raise MailReadError("IMAP folder search failed") from exc
                    errors.append({"folder": name, "error": "IMAP folder search failed"})
            page = matches[offset : offset + limit]
            messages = []
            for name in dict.fromkeys(hit[0] for hit in page):
                hits = [hit for hit in page if hit[0] == name]
                current = hits[0][1]
                try:
                    if folder is None:
                        selected = client.select_folder(name, readonly=True)
                        if selected.get(b"UIDVALIDITY") != current:
                            raise MailReadError("UIDVALIDITY changed; search again")
                    data = client.fetch(
                        [hit[2] for hit in hits], [HEADER_FETCH, "FLAGS", "RFC822.SIZE"]
                    )
                    for _, _, uid in hits:
                        if uid in data and HEADER_KEY in data[uid]:
                            messages.append(
                                summary(
                                    parse_message(data[uid][HEADER_KEY]),
                                    uid,
                                    data[uid].get(b"FLAGS", ()),
                                    data[uid].get(b"RFC822.SIZE", 0),
                                )
                                | {
                                    "account": self.account.name,
                                    "folder": name,
                                    "uidvalidity": current,
                                }
                            )
                except Exception as exc:
                    if folder is not None:
                        raise MailReadError("IMAP header read failed") from exc
                    errors.append(
                        {"folder": name, "error": "IMAP header read failed; search again"}
                    )
            result = {
                "account": self.account.name,
                "folder": folder,
                "uidvalidity": validity,
                "folders_queried": searched,
                "total": len(matches),
                "offset": offset,
                "order": "folder name ascending, then UID descending",
                "messages": messages,
                "next_offset": offset + limit if offset + limit < len(matches) else None,
                "partial": bool(errors),
            }
            if errors:
                result["errors"] = errors
            return result

    def _message(self, client, uid):
        if type(uid) is not int or not 1 <= uid <= 4294967295:
            raise MailReadError("Invalid UID")
        metadata = client.fetch([uid], ["RFC822.SIZE", "FLAGS"]).get(uid, {})
        size = metadata.get(b"RFC822.SIZE")
        if size is None:
            raise MailReadError("Message not found")
        if size > self.settings.max_message_bytes:
            raise MailReadError("Message exceeds configured size limit")
        data = client.fetch([uid], ["BODY.PEEK[]"]).get(uid, {})
        raw = data.get(b"BODY[]")
        if raw is None:
            raise MailReadError("Message no longer available")
        if len(raw) > self.settings.max_message_bytes:
            raise MailReadError("Message exceeds configured size limit")
        return parse_message(raw), metadata

    def get_message(self, folder, uid, uidvalidity):
        with self.session(folder, uidvalidity) as (client, validity):
            message, meta = self._message(client, uid)
            return {
                "account": self.account.name,
                "folder": folder,
                "uidvalidity": validity,
                "message": summary(message, uid, meta.get(b"FLAGS", ()), meta[b"RFC822.SIZE"])
                | body(message, self.settings.max_text_length)
                | {"attachments": attachments(message)},
            }

    def get_attachment(self, folder, uid, uidvalidity, index):
        with self.session(folder, uidvalidity) as (client, _):
            message, _ = self._message(client, uid)
            try:
                return extract_attachment(message, index, self.settings.max_attachment_bytes)
            except ValueError as exc:
                raise MailReadError(str(exc)) from exc

    def get_thread(self, folder, uid, uidvalidity, limit=50):
        if type(limit) is not int or not 1 <= limit <= self.settings.max_results:
            raise MailReadError("Invalid thread limit")
        with self.session(folder, uidvalidity) as (client, validity):
            seed, _ = self._message(client, uid)
            all_uids = sorted(client.search(["NOT", "DELETED"]), reverse=True)
            scanned = all_uids[: self.settings.max_thread_messages]
            if uid not in scanned:
                scanned.append(uid)
            headers = {}
            for start in range(0, len(scanned), 100):
                headers.update(
                    client.fetch(
                        scanned[start : start + 100], [HEADER_FETCH, "FLAGS", "RFC822.SIZE"]
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
                    )
                    for key in ordered[:limit]
                    if key in parsed
                ],
            }
