"""IMAP mail accounts (Gmail, Naver, Daum, …) with an app password.

Gmail cannot be read through Microsoft Graph. Gmail's own API needs a Google
Cloud project and, for mail scopes, Google's security review (unreviewed apps
are logged out every 7 days) — too heavy for professors. IMAP with an *app
password* works with any provider and needs no app registration:

  * the password is stored in the OS credential store (Windows Credential
    Manager / macOS Keychain) via `keyring`, never in files or logs;
  * mail is read with BODY.PEEK so nothing is marked as read;
  * "cleanup" moves a message to the provider's Trash folder (\\Trash special-use),
    so it can be restored, exactly like Deleted Items in Outlook.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

import keyring

from .config import Settings
from .models import Email

log = logging.getLogger(__name__)
KEYRING_SERVICE = "kmu-mail-agent"

PRESETS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "naver.com": "imap.naver.com",
    "daum.net": "imap.daum.net", "hanmail.net": "imap.daum.net",
    "kakao.com": "imap.kakao.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
}
APP_PASSWORD_HELP = {
    "imap.gmail.com": "Google 계정 → 보안 → 2단계 인증을 켠 뒤 https://myaccount.google.com/apppasswords 에서 앱 비밀번호(16자리)를 만드세요.",
    "imap.naver.com": "네이버 메일 → 환경설정 → POP3/IMAP 설정에서 IMAP 사용을 켜고, 2단계 인증을 쓰면 '애플리케이션 비밀번호'를 만드세요.",
    "imap.daum.net": "다음 메일 → 설정 → IMAP/POP3에서 IMAP 사용을 켜세요.",
}


def guess_host(username: str) -> str | None:
    return PRESETS.get(username.rsplit("@", 1)[-1].lower())


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def _body_text(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", "replace")
    text = _html_to_text(content) if part.get_content_type() == "text/html" else content
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def _addr(value: str | None) -> tuple[str, str]:
    name, addr = email.utils.parseaddr(value or "")
    return name, addr


class ImapAccountStore:
    """Which IMAP accounts are connected (data/imap_accounts.json). Passwords live in keyring."""

    def __init__(self, settings: Settings):
        self.path: Path = settings.data_dir / "imap_accounts.json"
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        with self._lock:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def get(self, username: str) -> dict | None:
        return next((a for a in self.list() if a["username"].lower() == username.lower()), None)

    def add(self, username: str, password: str, host: str | None = None, port: int = 993) -> dict:
        host = host or guess_host(username)
        if not host:
            raise ValueError("이 메일 주소의 IMAP 서버를 알 수 없습니다. 서버 주소(예: imap.example.com)를 함께 입력하세요.")
        client = ImapClient(username, host, port, password=password)
        info = client.check()  # raises with a readable message if login fails
        keyring.set_password(KEYRING_SERVICE, username.lower(), password)
        entry = {"username": username, "host": host, "port": port}
        with self._lock:
            accounts = [a for a in (json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else [])
                        if a["username"].lower() != username.lower()]
            accounts.append(entry)
            self.path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**entry, **info}

    def remove(self, username: str) -> bool:
        accounts = self.list()
        keep = [a for a in accounts if a["username"].lower() != username.lower()]
        if len(keep) == len(accounts):
            return False
        with self._lock:
            self.path.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            keyring.delete_password(KEYRING_SERVICE, username.lower())
        except Exception:
            pass
        return True

    def clients(self) -> list["ImapClient"]:
        return [ImapClient(a["username"], a["host"], a.get("port", 993)) for a in self.list()]


class ImapClient:
    kind = "imap"

    def __init__(self, username: str, host: str, port: int = 993, password: str | None = None):
        self.username, self.host, self.port = username, host, port
        self._password = password

    def _connect(self) -> imaplib.IMAP4_SSL:
        password = self._password or keyring.get_password(KEYRING_SERVICE, self.username.lower())
        if not password:
            raise RuntimeError("저장된 앱 비밀번호가 없습니다. 계정을 다시 연결하세요.")
        conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=60)
        try:
            conn.login(self.username, password)
        except imaplib.IMAP4.error as e:
            conn.logout()
            hint = APP_PASSWORD_HELP.get(self.host, "")
            raise RuntimeError(f"로그인 실패: 일반 비밀번호가 아니라 앱 비밀번호가 필요합니다. {hint}".strip()) from e
        return conn

    def check(self) -> dict:
        conn = self._connect()
        try:
            typ, data = conn.status("INBOX", "(MESSAGES UNSEEN)")
            raw = data[0].decode() if data and data[0] else ""
            total = int(re.search(r"MESSAGES (\d+)", raw).group(1)) if "MESSAGES" in raw else 0
            unseen = int(re.search(r"UNSEEN (\d+)", raw).group(1)) if "UNSEEN" in raw else 0
            return {"total": total, "unread": unseen}
        finally:
            conn.logout()

    def fetch(self, since: datetime, limit: int) -> list[Email]:
        conn = self._connect()
        out: list[Email] = []
        try:
            conn.select("INBOX", readonly=True)
            uidvalidity = (conn.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
            typ, data = conn.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
            uids = (data[0] or b"").split()[-limit:]
            for uid in reversed(uids):  # newest first
                typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[] INTERNALDATE)")
                raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                try:
                    received = parsedate_to_datetime(msg["Date"]) if msg["Date"] else datetime.now(timezone.utc)
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                except Exception:
                    received = datetime.now(timezone.utc)
                if received < since:
                    continue
                name, addr = _addr(msg["From"])
                out.append(Email(
                    id=f"imap:{self.username.lower()}:{uidvalidity}:{uid.decode()}",
                    subject=str(msg["Subject"] or ""),
                    sender_name=name, sender_email=addr,
                    to=[a for _, a in email.utils.getaddresses([str(msg["To"] or "")])][:20],
                    cc=[a for _, a in email.utils.getaddresses([str(msg["Cc"] or "")])][:20],
                    received_at=received,
                    body=_body_text(msg),
                    importance="high" if str(msg.get("Importance", "")).lower() == "high"
                    or str(msg.get("X-Priority", "")).startswith("1") else "normal",
                    has_attachments=any(p.get_content_disposition() == "attachment" for p in msg.walk()),
                    internet_message_id=str(msg["Message-ID"] or "") or None,
                    account=self.username,
                ))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return out

    def _trash_mailbox(self, conn) -> str:
        typ, boxes = conn.list()
        for b in boxes or []:
            line = b.decode(errors="replace")
            if "\\Trash" in line:
                return line.rsplit(' "', 1)[-1].rstrip('"') if line.endswith('"') else line.split()[-1]
        for fallback in ("[Gmail]/Trash", "Trash", "Deleted Messages", "휴지통"):
            if conn.select(f'"{fallback}"', readonly=True)[0] == "OK":
                return fallback
        raise RuntimeError("휴지통 폴더를 찾지 못했습니다.")

    def move_to_deleted(self, message_id: str) -> str:
        """Move a message to the provider's Trash (restorable)."""
        _, user, uidvalidity, uid = message_id.split(":", 3)
        conn = self._connect()
        try:
            trash = self._trash_mailbox(conn)
            conn.select("INBOX")
            current = (conn.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
            if current != uidvalidity:
                raise RuntimeError("메일함이 바뀌어 이 메일을 찾을 수 없습니다.")
            if "MOVE" in tuple(str(c).upper() for c in (conn.capabilities or ())):
                typ, _ = conn.uid("MOVE", uid, f'"{trash}"')
            else:
                typ, _ = conn.uid("COPY", uid, f'"{trash}"')
                if typ == "OK":
                    conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                    conn.expunge()
            if typ != "OK":
                raise RuntimeError("휴지통으로 옮기지 못했습니다.")
            return trash
        finally:
            try:
                conn.logout()
            except Exception:
                pass


class ImapMailSource:
    name = "imap"

    def __init__(self, clients: list[ImapClient], settings: Settings):
        self.clients, self.s = clients, settings

    def fetch(self, since: datetime | None = None) -> list[Email]:
        since = since or datetime.now(timezone.utc) - timedelta(hours=self.s.lookback_hours)
        out: list[Email] = []
        for c in self.clients:
            try:
                out += c.fetch(since, self.s.max_messages)
            except Exception as e:  # one broken account must not stop the others
                log.error("imap fetch failed for %s: %s", c.username, e)
                if len(self.clients) == 1:
                    raise
        return out


class CombinedMailSource:
    """Microsoft (Graph) + IMAP accounts in one pass."""

    name = "mail"

    def __init__(self, sources: list):
        self.sources = [s for s in sources if s is not None]

    def fetch(self, since: datetime | None = None) -> list[Email]:
        out: list[Email] = []
        for src in self.sources:
            try:
                out += src.fetch(since)
            except Exception as e:
                log.error("mail source %s failed: %s", getattr(src, "name", src), e)
                if len(self.sources) == 1:
                    raise
        return out
