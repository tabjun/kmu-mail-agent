"""Microsoft Graph access for a school (work/school) account such as @ms.kmu.ac.kr.

Auth: MSAL public client + device code flow (no client secret on the user's PC).
The token cache is stored in data/msal_token_cache.json so the daily job runs
silently after the first sign-in (refresh tokens last ~90 days of inactivity).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import msal

from .config import Settings
from .models import Email

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
# Mail.ReadWrite: read + move unwanted mail to Deleted Items (recoverable; never hard-deletes).
# Calendars.ReadWrite: events and the daily checklist. Mail.Send: digest to yourself.
SCOPES = ["Mail.ReadWrite", "Calendars.ReadWrite", "Mail.Send", "User.Read"]


class GraphAuth:
    def __init__(self, settings: Settings):
        if not settings.ms_client_id:
            raise RuntimeError(
                "MS_CLIENT_ID가 없습니다. README의 'Microsoft 앱 등록' 절차대로 Entra ID 앱을 만들고 .env에 넣으세요."
            )
        self.cache_path: Path = settings.msal_cache_path
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self.cache.deserialize(self.cache_path.read_text(encoding="utf-8"))
        self.app = msal.PublicClientApplication(
            settings.ms_client_id,
            authority=f"https://login.microsoftonline.com/{settings.ms_tenant}",
            token_cache=self.cache,
        )

    def _save(self) -> None:
        if self.cache.has_state_changed:
            self.cache_path.write_text(self.cache.serialize(), encoding="utf-8")

    # -- accounts --------------------------------------------------------
    def accounts(self) -> list[str]:
        """Usernames signed in on this PC (work/school and personal Microsoft accounts)."""
        return [a["username"] for a in self.app.get_accounts()]

    def add_account(self) -> str:
        """Device-code sign-in for one more account. Returns its username."""
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"device flow 시작 실패: {flow}")
        print(flow["message"], flush=True)  # "To sign in, use a web browser to open ... and enter the code ..."
        result = self.app.acquire_token_by_device_flow(flow)
        self._save()
        if "access_token" not in result:
            raise RuntimeError(f"토큰 발급 실패: {result.get('error')}: {result.get('error_description')}")
        return (result.get("id_token_claims") or {}).get("preferred_username", "")

    def remove_account(self, username: str) -> bool:
        for a in self.app.get_accounts(username=username):
            self.app.remove_account(a)
            self._save()
            return True
        return False

    def token(self, username: str | None = None) -> str:
        accounts = self.app.get_accounts(username=username) if username else self.app.get_accounts()
        if not accounts:
            raise RuntimeError(
                f"Microsoft 로그인 없음{f' ({username})' if username else ''}: `python -m mail_agent auth`로 계정을 추가하세요."
            )
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        self._save()
        if not result or "access_token" not in result:
            raise RuntimeError(f"{accounts[0]['username']} 토큰 갱신 실패: `python -m mail_agent auth`로 다시 로그인하세요.")
        return result["access_token"]


class GraphClient:
    """Graph calls on behalf of one signed-in account."""

    def __init__(self, auth: GraphAuth, username: str | None = None):
        self.auth = auth
        self.username = username or (auth.accounts() or [None])[0]
        self.http = httpx.Client(timeout=60)

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.auth.token(self.username)}"}
        if extra:
            h.update(extra)
        return h

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        r = self.http.get(url if url.startswith("http") else GRAPH + url, params=params, headers=self._headers(headers))
        r.raise_for_status()
        return r.json()

    def post(self, path: str, json: dict) -> dict:
        r = self.http.post(GRAPH + path, json=json, headers=self._headers({"Content-Type": "application/json"}))
        r.raise_for_status()
        return r.json() if r.content else {}

    def patch(self, path: str, json: dict) -> dict:
        r = self.http.patch(GRAPH + path, json=json, headers=self._headers({"Content-Type": "application/json"}))
        r.raise_for_status()
        return r.json() if r.content else {}

    def move_to_deleted(self, message_id: str) -> str:
        """Move a mail to Deleted Items (the user can restore it from there)."""
        return self.post(f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"}).get("id", "")

    def me(self) -> dict:
        return self.get("/me", params={"$select": "displayName,mail,userPrincipalName"})


def _addr(a: dict | None) -> tuple[str, str]:
    e = (a or {}).get("emailAddress") or {}
    return e.get("name", ""), e.get("address", "")


class GraphMailSource:
    """Reads inbox mail received since `since` from one or more signed-in accounts."""

    name = "graph"

    def __init__(self, clients: GraphClient | list[GraphClient], settings: Settings):
        self.clients = clients if isinstance(clients, list) else [clients]
        self.s = settings

    def fetch(self, since: datetime | None = None) -> list[Email]:
        emails: list[Email] = []
        for client in self.clients:
            try:
                emails += self._fetch_one(client, since)
            except Exception as e:  # one broken account must not stop the others
                log.error("mail fetch failed for %s: %s", client.username, e)
                if len(self.clients) == 1:
                    raise
        return emails

    def _fetch_one(self, client: GraphClient, since: datetime | None) -> list[Email]:
        since = since or datetime.now(timezone.utc) - timedelta(hours=self.s.lookback_hours)
        params = {
            "$filter": f"receivedDateTime ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$orderby": "receivedDateTime desc",
            "$top": "50",
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,importance,"
                       "isRead,flag,hasAttachments,webLink,conversationId,internetMessageId",
        }
        # Ask Graph for plain-text bodies instead of HTML.
        headers = {"Prefer": 'outlook.body-content-type="text"'}
        url, emails = "/me/mailFolders/inbox/messages", []
        while url and len(emails) < self.s.max_messages:
            data = client.get(url, params=params, headers=headers)
            params = None  # nextLink already carries the query
            for m in data.get("value", []):
                name, addr = _addr(m.get("from"))
                emails.append(Email(
                    id=m["id"],
                    subject=m.get("subject") or "",
                    sender_name=name, sender_email=addr,
                    to=[_addr(r)[1] for r in m.get("toRecipients", [])],
                    cc=[_addr(r)[1] for r in m.get("ccRecipients", [])],
                    received_at=datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")),
                    body=((m.get("body") or {}).get("content") or "").strip(),
                    importance=m.get("importance", "normal"),
                    is_read=m.get("isRead", False),
                    flagged=((m.get("flag") or {}).get("flagStatus") == "flagged"),
                    has_attachments=m.get("hasAttachments", False),
                    web_link=m.get("webLink"),
                    conversation_id=m.get("conversationId"),
                    internet_message_id=m.get("internetMessageId"),
                    account=client.username,
                ))
            url = data.get("@odata.nextLink")
        return emails[: self.s.max_messages]


class MailActions:
    """Mailbox-changing operations, routed to the account that owns each message."""

    def __init__(self, clients: list[GraphClient]):
        self.by_account = {c.username.lower(): c for c in clients if c.username}
        self.default = clients[0] if clients else None

    def move_to_deleted(self, account: str | None, message_id: str) -> str:
        client = self.by_account.get((account or "").lower(), self.default)
        if client is None:
            raise RuntimeError("no signed-in account for this message")
        return client.move_to_deleted(message_id)
