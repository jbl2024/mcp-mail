import base64
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest
import yaml

from mcp_mail.config import ConfigError, load_config
from mcp_mail.models import HEADER_FETCH, HEADER_KEY, body, parse_message
from mcp_mail.reader import MailReader, MailReadError
from mcp_mail.service import MailService


def mail(identifier="root", references=""):
    msg = EmailMessage()
    msg["Message-ID"] = f"<{identifier}@example.test>"
    msg["Subject"] = "Example résumé"
    msg["From"] = "sender@example.test"
    msg["To"] = "recipient@example.test"
    if references:
        msg["References"] = references
    msg.set_content("Plain message")
    msg.add_alternative("<h1>Title</h1><p>Hello <strong>mail</strong></p>", subtype="html")
    msg.add_attachment(
        b"fictional attachment",
        maintype="application",
        subtype="octet-stream",
        filename="../example.bin",
    )
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, *args, **kwargs):
        assert kwargs["use_uid"] is True
        self.calls = [("connect", kwargs)]
        self.messages = {1: mail(), 2: mail("reply", "<root@example.test>"), 3: mail("other")}
        self.validity = 7
        self.fail = False

    def starttls(self, context):
        self.calls.append(("starttls", context))

    def login(self, *args):
        self.calls.append(("login",))

    def logout(self):
        self.calls.append(("logout",))

    def select_folder(self, folder, readonly):
        assert readonly is True
        self.calls.append(("examine", folder))
        return {b"UIDVALIDITY": self.validity}

    def list_folders(self):
        return [((), b"/", "INBOX"), ((), b"/", "Private")]

    def search(self, criteria, charset=None):
        self.calls.append(("search", criteria, charset))
        if self.fail:
            raise RuntimeError("sensitive server details")
        return list(self.messages)

    def fetch(self, uids, selectors):
        assert set(selectors) <= {HEADER_FETCH, "FLAGS", "RFC822.SIZE", "BODY.PEEK[]"}
        self.calls.append(("fetch", list(uids), selectors))
        result = {}
        for uid in uids:
            if uid not in self.messages:
                continue
            raw = self.messages[uid]
            result[uid] = {}
            for selector in selectors:
                if selector == HEADER_FETCH:
                    result[uid][HEADER_KEY] = raw.split(b"\n\n", 1)[0] + b"\n\n"
                elif selector == "BODY.PEEK[]":
                    result[uid][b"BODY[]"] = raw
                elif selector == "FLAGS":
                    result[uid][b"FLAGS"] = (b"\\Flagged",) if uid == 1 else ()
                else:
                    result[uid][b"RFC822.SIZE"] = len(raw)
        return result


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("MAIL_USERNAME", "mail-user@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", "fictional-test-value")
    config = load_config("config.example.yaml")
    return replace(
        config, accounts={"primary": replace(config.accounts["primary"], folders=("INBOX",))}
    )


@pytest.fixture
def reader(config):
    client = FakeIMAP(use_uid=True)
    value = MailReader(
        config.accounts["primary"],
        config.credentials["default"],
        config.settings,
        client_factory=lambda *args, **kwargs: _factory(client, kwargs),
    )
    value.fake = client
    return value


def test_search_pagination_filters_and_header_only(reader):
    result = reader.search_messages(
        folder="INBOX",
        seen=False,
        flagged=True,
        important=True,
        query="résumé",
        since="2026-01-01",
        before="2026-02-01",
        limit=1,
        offset=1,
    )
    assert result["messages"][0]["uid"] == 2
    assert result["next_offset"] == 2
    assert result["uidvalidity"] == 7
    search = next(c for c in reader.fake.calls if c[0] == "search")
    assert "UNSEEN" in search[1] and "FLAGGED" in search[1]
    assert ["OR", "FLAGGED", ["KEYWORD", "$Important"]] in search[1]
    assert search[2] == "UTF-8"
    assert all("BODY.PEEK[]" not in c[2] for c in reader.fake.calls if c[0] == "fetch")


def test_read_and_extract_do_not_change_flags(reader):
    result = reader.get_message("INBOX", 1, 7)
    assert "Plain message" in result["message"]["markdown"]
    assert result["message"]["flagged"] is True
    assert result["message"]["seen"] is False
    result = reader.get_attachment("INBOX", 1, 7, 0)
    assert base64.b64decode(result["data"]) == b"fictional attachment"
    assert result["filename"] == "../example.bin"  # metadata only, never a filesystem path
    assert reader.fake.calls[-1] == ("logout",)


def test_html_conversion():
    msg = EmailMessage()
    msg.set_content("<h1>Title</h1><p>Hello <b>mail</b></p><script>bad()</script>", subtype="html")
    result = body(parse_message(msg.as_bytes()), 1000)
    assert "# Title" in result["markdown"]
    assert "**mail**" in result["markdown"]
    assert "bad()" not in result["markdown"]
    assert body(parse_message(msg.as_bytes()), 2)["body_truncated"]


def test_thread_links_not_subject(reader):
    result = reader.get_thread("INBOX", 1, 7)
    assert [m["uid"] for m in result["messages"]] == [1, 2]
    assert not result["scan_truncated"]


def test_thread_scan_limit(reader):
    reader.settings = replace(reader.settings, max_thread_messages=1)
    assert reader.get_thread("INBOX", 1, 7)["scan_truncated"]


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_message", ("INBOX", 1, 8)),
        ("get_message", ("Private", 1, 7)),
        ("get_message", ("INBOX", 999, 7)),
        ("get_attachment", ("INBOX", 1, 7, -1)),
    ],
)
def test_bad_identity_and_allowlist(reader, method, args):
    with pytest.raises(MailReadError):
        getattr(reader, method)(*args)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"offset": -1},
        {"query": "\r\n"},
        {"since": "bad"},
        {"seen": "yes"},
        {"important": "yes"},
        {"since": "2026-02-01", "before": "2026-01-01"},
    ],
)
def test_search_validation(reader, kwargs):
    with pytest.raises(MailReadError):
        reader.search_messages(**kwargs)


def test_size_limits(reader):
    reader.settings = replace(reader.settings, max_message_bytes=1)
    with pytest.raises(MailReadError, match="size limit"):
        reader.get_message("INBOX", 1, 7)
    assert all("BODY.PEEK[]" not in c[2] for c in reader.fake.calls if c[0] == "fetch")
    reader.settings = replace(reader.settings, max_message_bytes=100000, max_attachment_bytes=1)
    with pytest.raises(MailReadError, match="size limit"):
        reader.get_attachment("INBOX", 1, 7, 0)


def test_failure_is_sanitized_and_logs_out(reader):
    reader.fake.fail = True
    with pytest.raises(MailReadError) as error:
        reader.search_messages(folder="INBOX")
    assert "sensitive" not in str(error.value)
    assert reader.fake.calls[-1] == ("logout",)


def test_starttls_before_login(reader):
    reader.account = replace(reader.account, security="starttls")
    reader.list_folders()
    names = [c[0] for c in reader.fake.calls]
    assert names.index("starttls") < names.index("login")


def test_only_allowlisted_folders(reader):
    assert reader.list_folders() == [{"name": "INBOX", "selectable": True}]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["accounts"][0].update(security="plain"),
        lambda c: c["accounts"][0].update(host="https://imap.example.test"),
        lambda c: c["accounts"][0].update(folders=[]),
        lambda c: c["accounts"].append(c["accounts"][0]),
        lambda c: c["settings"].update(max_results=True),
        lambda c: c.update(unknown_section=[]),
        lambda c: c["credentials"]["default"].update(password="forbidden"),
    ],
)
def test_config_rejects_unsafe_values(tmp_path, mutation):
    raw = yaml.safe_load(Path("config.example.yaml").read_text())
    mutation(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError):
        load_config(path)


async def test_service(config, reader):
    service = MailService(config)
    service.readers["primary"] = reader
    result = await service.call("get_message", "primary", folder="INBOX", uid=1, uidvalidity=7)
    assert result["content_is_untrusted"]
    with pytest.raises(MailReadError):
        await service.call("get_message", "primary", folder="INBOX", uid=1, uidvalidity=None)
    with pytest.raises(MailReadError):
        await service.call("search_messages", "unknown")


async def test_mcp_tools_are_read_only():
    from mcp_mail.server import mcp

    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "list_accounts",
        "list_folders",
        "search_messages",
        "get_message",
        "get_attachment",
        "get_thread",
    }
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)


def _factory(client, kwargs):
    assert kwargs["use_uid"] is True
    assert kwargs["timeout"] > 0
    return client


def test_attached_message_and_inline_image(reader):
    message = EmailMessage()
    message.set_content("Outer body")
    inner = EmailMessage()
    inner.set_content("Attached body")
    message.add_attachment(inner, filename="example.eml")
    message.add_attachment(
        b"fictional image",
        maintype="image",
        subtype="png",
        disposition="inline",
        cid="<image@example.test>",
    )
    reader.fake.messages[1] = message.as_bytes()
    result = reader.get_message("INBOX", 1, 7)["message"]
    assert len(result["attachments"]) == 2
    assert "Outer body" in result["markdown"]
    payload = reader.get_attachment("INBOX", 1, 7, 0)
    assert b"Attached body" in base64.b64decode(payload["data"])


def test_imapclient_generates_peek_and_readonly_selection(monkeypatch):
    from unittest.mock import MagicMock

    from imapclient import IMAPClient

    transport = MagicMock()
    transport.select.return_value = ("OK", [b"1"])
    transport.untagged_responses = {
        "EXISTS": [b"1"],
        "RECENT": [b"0"],
        "FLAGS": [b"()"],
        "UIDVALIDITY": [b"7"],
    }
    transport._command_complete.return_value = ("OK", [b"completed"])
    transport._untagged_response.return_value = ("OK", [(b"1 (UID 1 BODY[] {4}", b"test"), b")"])
    monkeypatch.setattr(IMAPClient, "_create_IMAP4", lambda self: transport)
    client = IMAPClient("imap.example.test", use_uid=True, timeout=20)
    assert client.select_folder("INBOX", readonly=True)[b"UIDVALIDITY"] == 7
    transport.select.assert_called_once_with(b'"INBOX"', True)
    assert client.fetch([1], ["BODY.PEEK[]"])[1][b"BODY[]"] == b"test"
    transport._command.assert_called_once_with("UID", "FETCH", b"1", "(BODY.PEEK[])", None)


async def test_smoke_without_mcp(config, reader, monkeypatch, capsys):
    from argparse import Namespace

    from mcp_mail import smoke

    service = MailService(config)
    service.readers["primary"] = reader
    monkeypatch.setattr(smoke, "MailService", lambda config: service)
    await smoke.run(Namespace(config="config.example.yaml"))
    output = capsys.readouterr().out
    assert '"status": "ok"' in output
    assert "Plain message" not in output and "sender@example.test" not in output


def test_smoke_requires_live(monkeypatch):
    from mcp_mail.smoke import main

    monkeypatch.setattr("sys.argv", ["mail-smoke"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


async def test_mcp_read_tool(config, reader, monkeypatch):
    from mcp_mail import server

    service = MailService(config)
    service.readers["primary"] = reader
    monkeypatch.setattr(server, "_service", lambda: service)
    response = await server.get_message("primary", "INBOX", 1, 7)
    assert response["result"]["message"]["subject"] == "Example résumé"
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        await server.get_message("primary", "INBOX", 1, 8)


def test_reader_has_only_allowlisted_imap_calls():
    import ast
    import inspect

    import mcp_mail.reader

    tree = ast.parse(inspect.getsource(mcp_mail.reader))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
    }
    assert calls <= {
        "starttls",
        "login",
        "select_folder",
        "logout",
        "list_folders",
        "search",
        "fetch",
    }
    assert "select_folder" in calls and "fetch" in calls


def test_no_folder_restriction_by_default():
    config = load_config("config.example.yaml")
    assert config.accounts["primary"].folders is None
    assert MailService(config).list_accounts()["accounts"][0]["folders"] is None


class MultiFolderIMAP(FakeIMAP):
    def __init__(self):
        super().__init__(use_uid=True)
        self.folders = {
            "Archive": {1: mail("archived"), 2: mail("archived-reply")},
            "INBOX": {1: mail("inbox")},
            "Sent": {1: mail("sent")},
        }
        self.validities = {"Archive": 10, "INBOX": 20, "Sent": 30}
        self.current = None
        self.fail_folder = None
        self.change_validity = False
        self.selections = {}

    def list_folders(self):
        return [((), b"/", f) for f in self.folders] + [(("\\Noselect",), b"/", "Parent")]

    def select_folder(self, folder, readonly):
        assert readonly is True
        self.calls.append(("examine", folder))
        if folder == self.fail_folder:
            raise RuntimeError("private server details")
        self.current = folder
        self.messages = self.folders[folder]
        self.selections[folder] = self.selections.get(folder, 0) + 1
        validity = self.validities[folder]
        if self.change_validity and self.selections[folder] > 1:
            validity += 1
        return {b"UIDVALIDITY": validity}


@pytest.fixture
def multi_reader(config):
    client = MultiFolderIMAP()
    value = MailReader(
        replace(config.accounts["primary"], folders=None),
        config.credentials["default"],
        config.settings,
        client_factory=lambda *args, **kwargs: _factory(client, kwargs),
    )
    value.fake = client
    return value


def test_discovers_unrestricted_folders(multi_reader):
    assert multi_reader.list_folders() == [
        {"name": "Archive", "selectable": True},
        {"name": "INBOX", "selectable": True},
        {"name": "Parent", "selectable": False},
        {"name": "Sent", "selectable": True},
    ]


def test_all_folders_search_and_global_pagination(multi_reader):
    result = multi_reader.search_messages(limit=2, offset=1, seen=False)
    assert result["total"] == 4
    assert result["folders_queried"] == ["Archive", "INBOX", "Sent"]
    assert [(m["folder"], m["uid"], m["uidvalidity"]) for m in result["messages"]] == [
        ("Archive", 1, 10),
        ("INBOX", 1, 20),
    ]
    assert result["next_offset"] == 3
    assert not result["partial"]
    searches = [call for call in multi_reader.fake.calls if call[0] == "search"]
    assert len(searches) == 3 and all("UNSEEN" in call[1] for call in searches)
    fetched = [call for call in multi_reader.fake.calls if call[0] == "fetch"]
    assert sum(len(call[1]) for call in fetched) == 2
    assert ("examine", "Parent") not in multi_reader.fake.calls
    last = multi_reader.search_messages(limit=2, offset=3)
    assert [m["folder"] for m in last["messages"]] == ["Sent"]
    assert last["next_offset"] is None


def test_explicit_folder_searches_only_that_folder(multi_reader):
    result = multi_reader.search_messages(folder="Sent")
    assert result["folders_queried"] == ["Sent"]
    assert result["uidvalidity"] == 30
    assert len([c for c in multi_reader.fake.calls if c[0] == "search"]) == 1
    assert multi_reader.get_message("Sent", 1, 30)["message"]["message_id"] == "<sent@example.test>"


def test_optional_allowlist_applies_to_discovery_search_and_reads(multi_reader):
    multi_reader.account = replace(multi_reader.account, folders=("INBOX",))
    assert multi_reader.list_folders() == [{"name": "INBOX", "selectable": True}]
    assert multi_reader.search_messages()["folders_queried"] == ["INBOX"]
    with pytest.raises(MailReadError, match="allowlist"):
        multi_reader.search_messages(folder="Sent")
    with pytest.raises(MailReadError, match="allowlist"):
        multi_reader.get_message("Sent", 1, 30)


def test_global_search_reports_partial_errors(multi_reader):
    multi_reader.fake.fail_folder = "Archive"
    result = multi_reader.search_messages()
    assert result["partial"]
    assert result["total"] == 2
    assert result["errors"] == [{"folder": "Archive", "error": "IMAP folder search failed"}]
    assert "private" not in str(result)
    assert result["folders_queried"] == ["INBOX", "Sent"]


def test_uidvalidity_change_between_search_and_fetch(multi_reader):
    multi_reader.fake.change_validity = True
    result = multi_reader.search_messages()
    assert result["partial"]
    assert result["messages"] == []
    assert len(result["errors"]) == 3
    assert not any(c[0] == "fetch" for c in multi_reader.fake.calls)


def test_no_selectable_folders(multi_reader):
    multi_reader.fake.folders = {}
    result = multi_reader.search_messages()
    assert result["total"] == 0 and result["messages"] == []
    assert result["folders_queried"] == []
    assert not result["partial"]


async def test_smoke_discovers_folders(config, multi_reader, monkeypatch, capsys):
    from argparse import Namespace

    from mcp_mail import smoke

    service = MailService(config)
    service.readers["primary"] = multi_reader
    monkeypatch.setattr(smoke, "MailService", lambda config: service)
    await smoke.run(Namespace(config="config.example.yaml"))
    output = capsys.readouterr().out
    assert output.count('"status": "ok"') == 3
    assert "Parent" not in output
