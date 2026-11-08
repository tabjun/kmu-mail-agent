"""Google Calendar for Gmail accounts.

Mail from a Gmail account (read over IMAP) should land in *that* account's calendar,
which is the Google Calendar — the one the user's phone and Outlook desktop already
show for the Gmail address. Writing to it needs OAuth (Google has no app-password
access to calendars), so:

  * GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (an OAuth "Desktop app" client) live in .env
    and travel with the key ZIP, never in the public repo;
  * the user clicks "Google 캘린더 연결" once; the browser opens Google's consent page and the
    app receives the result on http://localhost (same flow as the Microsoft login);
  * one token file per account in data/google_tokens/<email>.json (refreshed automatically).

Scope is calendar.events only (plus openid/email to learn which account signed in).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/calendar.events"]
MARK = {"private": {"mailAgent": "1"}}  # lets us find our own events
SUCCESS_HTML = "✅ Google 캘린더가 연결되었습니다. 이 창을 닫고 메일 비서 화면으로 돌아가세요."


class GoogleAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir: Path = settings.data_dir / "google_tokens"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- setup -------------------------------------------------------------
    def configured(self) -> bool:
        return bool(self.settings.google_client_id and self.settings.google_client_secret)

    def _client_config(self) -> dict:
        return {"installed": {
            "client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}}

    def _path(self, email: str) -> Path:
        return self.dir / f"{email.lower()}.json"

    # -- accounts ----------------------------------------------------------
    def accounts(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def has(self, email: str | None) -> bool:
        return bool(email) and self._path(email).exists()

    def login_interactive(self, login_hint: str | None = None, timeout: int = 300) -> str:
        """Opens Google's consent page in this PC's browser; returns the signed-in email."""
        if not self.configured():
            raise RuntimeError("Google 캘린더 연결 정보(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)가 .env에 없습니다. "
                               "연구실에서 받은 키 파일을 다시 확인하세요.")
        import jwt
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_config(self._client_config(), SCOPES)
        extra = {"prompt": "select_account consent", "access_type": "offline"}
        if login_hint:
            extra["login_hint"] = login_hint
        creds = flow.run_local_server(port=0, open_browser=True, success_message=SUCCESS_HTML,
                                      authorization_prompt_message="", timeout_seconds=timeout, **extra)
        email = jwt.decode(creds.id_token, options={"verify_signature": False}).get("email", "") if creds.id_token else ""
        if not email:
            raise RuntimeError("Google 계정 주소를 확인하지 못했습니다. 다시 시도해 주세요.")
        self._path(email).write_text(creds.to_json(), encoding="utf-8")
        return email

    def credentials(self, email: str):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        path = self._path(email)
        if not path.exists():
            raise RuntimeError(f"Google 캘린더가 연결되지 않았습니다 ({email}). 설정 1단계에서 'Google 캘린더 연결'을 누르세요.")
        creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        if not creds.valid:
            if creds.refresh_token:
                creds.refresh(Request())
                path.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise RuntimeError(f"Google 캘린더 연결이 만료되었습니다 ({email}). 다시 연결해 주세요.")
        return creds

    def remove(self, email: str) -> bool:
        path = self._path(email)
        if path.exists():
            path.unlink()
            return True
        return False


def _gid(key: str) -> str:
    # Google event ids: base32hex chars (a-v, 0-9), 5-1024 long.
    return base64.b32hexencode(bytes.fromhex(key)).decode().lower().rstrip("=")


class GoogleCalendar:
    """CalendarSink for one Gmail account's primary Google Calendar."""
    name = "google"

    def __init__(self, settings: Settings, email: str, auth: GoogleAuth | None = None):
        from googleapiclient.discovery import build

        self.username = email
        self.auth = auth or GoogleAuth(settings)
        self.service = build("calendar", "v3", credentials=self.auth.credentials(email), cache_discovery=False)
        self.calendar_id = settings.google_calendar_id or "primary"
        self.tz = settings.timezone

    def _body(self, spec) -> dict:
        when = (lambda d: {"date": d.strftime("%Y-%m-%d")}) if spec.all_day else \
               (lambda d: {"dateTime": d.isoformat(), "timeZone": self.tz.key})
        minutes = sorted(set(getattr(spec, "reminders", ()) or (spec.reminder_minutes,)))[:5]  # Google: max 5
        body = {"summary": spec.title, "description": spec.body, "start": when(spec.start), "end": when(spec.end),
                "transparency": "transparent" if spec.kind in ("deadline", "checklist", "precheck", "reminder") else "opaque",
                "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in minutes]},
                "extendedProperties": MARK}
        if spec.location:
            body["location"] = spec.location
        return body

    def create(self, spec) -> str | None:
        gid = _gid(spec.key)
        try:
            return self.service.events().insert(calendarId=self.calendar_id, body={"id": gid, **self._body(spec)}).execute()["id"]
        except Exception as e:
            if "409" in str(e) or "duplicate" in str(e).lower():  # created on an earlier run / after a reset → refresh it
                return self.update(spec, gid)
            raise

    def update(self, spec, event_id: str) -> str | None:
        self.service.events().patch(calendarId=self.calendar_id, eventId=event_id, body=self._body(spec)).execute()
        return event_id

    def web_link(self, event_id: str) -> str | None:
        try:
            return self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute().get("htmlLink")
        except Exception:
            return None

    def upcoming(self, now_iso: str, limit: int = 50) -> list[dict]:
        r = self.service.events().list(calendarId=self.calendar_id, timeMin=now_iso, singleEvents=True, orderBy="startTime",
                                       privateExtendedProperty="mailAgent=1", maxResults=limit).execute()
        return r.get("items", [])
