"""IMAP (Gmail/Naver) source with a fake server: parsing, trash move, credential storage."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import mail_agent.imap_mail as im

RAW = (
    "From: =?utf-8?b?7Jew6rWs7LKY?= <research@kmu.ac.kr>\r\n"
    "To: prof@gmail.com\r\n"
    "Subject: =?utf-8?b?W+yXsOq1rOyymF0g7KCV7IKwIOymneu5mSDsoJzstpw=?=\r\n"
    "Date: Mon, 09 Nov 2026 09:00:00 +0900\r\n"
    "Message-ID: <abc123@kmu.ac.kr>\r\n"
    "Content-Type: text/html; charset=utf-8\r\n\r\n"
    "<p>11월 10일 17시까지 <b>업로드</b> 해주세요.</p>\r\n"
).encode("utf-8")


class FakeIMAP:
    capabilities = ("IMAP4REV1", "MOVE")
    calls: list = []
    password_ok = "app-pass"

    def __init__(self, host, port, timeout=None):
        self.host = host

    def login(self, user, password):
        if password != self.password_ok:
            raise im.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def logout(self):
        pass

    def status(self, box, what):
        return "OK", [b'INBOX (MESSAGES 42 UNSEEN 3)']

    def select(self, box, readonly=False):
        return "OK", [b"1"]

    def response(self, code):
        return code, [b"777"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Trash) "/" "[Gmail]/&1zTJwNG1-"']

    def uid(self, cmd, *args):
        FakeIMAP.calls.append((cmd, args))
        if cmd == "search":
            return "OK", [b"5"]
        if cmd == "fetch":
            return "OK", [(b"5 (UID 5 BODY[] {100}", RAW), b")"]
        return "OK", [b""]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    vault = {}
    monkeypatch.setattr(im.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(im.keyring, "set_password", lambda svc, u, p: vault.__setitem__((svc, u), p))
    monkeypatch.setattr(im.keyring, "get_password", lambda svc, u: vault.get((svc, u)))
    monkeypatch.setattr(im.keyring, "delete_password", lambda svc, u: vault.pop((svc, u), None))
    FakeIMAP.calls = []
    return vault


def test_fetch_parses_korean_mail(fake):
    c = im.ImapClient("prof@gmail.com", "imap.gmail.com", password="app-pass")
    mails = c.fetch(datetime(2026, 11, 1, tzinfo=timezone.utc), 50)
    assert len(mails) == 1
    m = mails[0]
    assert m.subject == "[연구처] 정산 증빙 제출" and m.sender_name == "연구처" and m.sender_email == "research@kmu.ac.kr"
    assert "11월 10일 17시까지" in m.body and "<b>" not in m.body  # html → text
    assert m.id == "imap:prof@gmail.com:777:5" and m.internet_message_id == "<abc123@kmu.ac.kr>"
    assert m.account == "prof@gmail.com"
    assert ("fetch", (b"5", "(BODY.PEEK[] INTERNALDATE)")) in FakeIMAP.calls  # PEEK: never marks as read


def test_move_to_trash_uses_special_use_folder(fake):
    c = im.ImapClient("prof@gmail.com", "imap.gmail.com", password="app-pass")
    assert c.move_to_deleted("imap:prof@gmail.com:777:5") == "[Gmail]/&1zTJwNG1-"
    assert ("MOVE", ("5", '"[Gmail]/&1zTJwNG1-"')) in FakeIMAP.calls


def test_account_store_keeps_password_in_keyring(fake, tmp_path):
    class S:
        data_dir = tmp_path

    store = im.ImapAccountStore(S())
    info = store.add("prof@gmail.com", "app-pass")
    assert info["host"] == "imap.gmail.com" and info["total"] == 42
    assert "app-pass" not in (tmp_path / "imap_accounts.json").read_text(encoding="utf-8")  # not in files
    assert fake[(im.KEYRING_SERVICE, "prof@gmail.com")] == "app-pass"
    assert [c.username for c in store.clients()] == ["prof@gmail.com"]
    with pytest.raises(RuntimeError, match="앱 비밀번호"):
        store.add("prof2@gmail.com", "wrong")
    assert store.remove("prof@gmail.com") and store.list() == []


def test_mail_actions_route_same_address_by_kind():
    from mail_agent.msgraph import MailActions

    class G:
        kind, username = "graph", "me@gmail.com"

        def move_to_deleted(self, mid):
            return "graph"

    class I:
        kind, username = "imap", "me@gmail.com"

        def move_to_deleted(self, mid):
            return "imap"

    ma = MailActions([G(), I()])
    assert ma.move_to_deleted("me@gmail.com", "imap:me@gmail.com:1:5") == "imap"
    assert ma.move_to_deleted("me@gmail.com", "AAMkAD123") == "graph"
