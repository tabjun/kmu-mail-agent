"""Runtime settings loaded from .env / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# USD per 1M tokens (input, output). Source: Claude API pricing, cached 2026-10.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Server-side refusal fallback ("default" form) is supported on these models only.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- Claude API -------------------------------------------------------
    # ROOT_API_KEY is the org (NHLab) key from the professor; ANTHROPIC_API_KEY also works.
    anthropic_api_key: str | None = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY") or _env("ROOT_API_KEY"), repr=False
    )
    # Org-scoped user keys (sk-ant-usr-...) must name a workspace via the anthropic-workspace-id header.
    anthropic_workspace_id: str | None = field(default_factory=lambda: (_env("ANTHROPIC_WORKSPACE_ID") or "").split("#")[0].strip() or None)
    triage_model: str = field(default_factory=lambda: _env("TRIAGE_MODEL", "claude-sonnet-5-5"))
    triage_effort: str = field(default_factory=lambda: _env("TRIAGE_EFFORT", "low"))
    digest_model: str = field(default_factory=lambda: _env("DIGEST_MODEL", "claude-sonnet-5-5"))
    digest_effort: str = field(default_factory=lambda: _env("DIGEST_EFFORT", "medium"))
    max_body_chars: int = field(default_factory=lambda: int(_env("MAX_BODY_CHARS", "12000")))
    triage_concurrency: int = field(default_factory=lambda: int(_env("TRIAGE_CONCURRENCY", "4")))

    # --- Microsoft 365 (school account, e.g. @ms.kmu.ac.kr) --------------
    ms_client_id: str | None = field(default_factory=lambda: _env("MS_CLIENT_ID"), repr=False)
    # "organizations" = any work/school tenant. Can also be the KMU tenant id or "ms.kmu.ac.kr".
    ms_tenant: str = field(default_factory=lambda: _env("MS_TENANT", "organizations"))

    # Which signed-in accounts to read ("all" or comma-separated usernames) and
    # whose Outlook calendar gets the events (default: the first mail account).
    mail_accounts: list[str] = field(
        default_factory=lambda: [a.strip() for a in _env("MAIL_ACCOUNTS", "all").split(",") if a.strip()]
    )
    calendar_account: str | None = field(default_factory=lambda: _env("CALENDAR_ACCOUNT"))
    # Daily "오늘 할 일" checklist event: HH:MM local time, reminder fires at start. Empty = off.
    checklist_time: str | None = field(default_factory=lambda: _env("CHECKLIST_TIME", "08:00"))

    # --- Google Calendar (optional) ---------------------------------------
    google_credentials_file: Path = field(
        default_factory=lambda: PROJECT_ROOT / _env("GOOGLE_CREDENTIALS_FILE", "secrets/google_credentials.json")
    )
    google_calendar_id: str = field(default_factory=lambda: _env("GOOGLE_CALENDAR_ID", "primary"))

    # --- Behaviour ----------------------------------------------------------
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo(_env("TIMEZONE", "Asia/Seoul")))
    calendar_provider: str = field(default_factory=lambda: _env("CALENDAR_PROVIDER", "outlook"))  # outlook|google|ics|none
    notifiers: list[str] = field(
        default_factory=lambda: [n.strip() for n in _env("NOTIFIERS", "console,file").split(",") if n.strip()]
    )
    webhook_url: str | None = field(default_factory=lambda: _env("NOTIFY_WEBHOOK_URL"), repr=False)
    digest_recipient: str | None = field(default_factory=lambda: _env("DIGEST_RECIPIENT"))  # default: the signed-in user
    auto_calendar_confidence: float = field(
        default_factory=lambda: float(_env("AUTO_CALENDAR_CONFIDENCE", "0.8"))
    )
    lookback_hours: int = field(default_factory=lambda: int(_env("LOOKBACK_HOURS", "36")))
    first_run_lookback_days: int = field(default_factory=lambda: int(_env("FIRST_RUN_LOOKBACK_DAYS", "7")))
    max_messages: int = field(default_factory=lambda: int(_env("MAX_MESSAGES", "200")))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))
    # "sample" reads samples/emails.json instead of Outlook (demos, screenshots, trying it without an account)
    mail_source: str = field(default_factory=lambda: _env("MAIL_SOURCE", "graph"))

    # --- Triage API (FastAPI, used by the Outlook add-in) --------------------
    # dev   : localhost-only, optional X-API-Key header (API_KEY)
    # entra : Bearer token from the add-in (NAA), validated against Microsoft Entra
    api_auth_mode: str = field(default_factory=lambda: _env("API_AUTH_MODE", "dev"))
    api_key: str | None = field(default_factory=lambda: _env("API_KEY"), repr=False)
    api_audience: str | None = field(default_factory=lambda: _env("API_AUDIENCE"))  # e.g. api://localhost:3000/<client-id>
    api_allowed_tenants: list[str] = field(
        default_factory=lambda: [t.strip() for t in (_env("API_ALLOWED_TENANTS") or "").split(",") if t.strip()]
    )
    api_allowed_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in _env("API_ALLOWED_ORIGINS", "https://localhost:3000").split(",")]
    )

    # --- Paths ----------------------------------------------------------------
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / _env("DATA_DIR", "data"))
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / _env("OUTPUT_DIR", "outputs"))
    rules_file: Path = field(default_factory=lambda: PROJECT_ROOT / _env("RULES_FILE", "config/rules.yaml"))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # Filled from data/preferences.json by apply_preferences()
    deadline_reminder_minutes: int = 24 * 60
    meeting_reminder_minutes: int = 30
    deadline_reminders: list[int] = field(default_factory=lambda: [24 * 60])
    meeting_reminders: list[int] = field(default_factory=lambda: [30])
    precheck_days: list[int] = field(default_factory=lambda: [3, 1])
    precheck_time: str = "09:00"
    cleanup_mode: str = "ask"

    @property
    def prefs_path(self) -> Path:
        return self.data_dir / "preferences.json"

    def apply_preferences(self, prefs=None):
        """Overlay the chatbot-managed preferences on top of .env defaults."""
        from .preferences import PreferencesStore

        p = prefs or PreferencesStore(self.prefs_path).load()
        self.triage_model = p.triage_model
        self.mail_accounts = p.accounts or ["all"]
        self.calendar_account = p.calendar_account or self.calendar_account
        self.checklist_time = p.calendar.checklist_time
        self.auto_calendar_confidence = p.calendar.auto_add_confidence
        self.deadline_reminders = p.calendar.deadline_list()
        self.meeting_reminders = p.calendar.meeting_list()
        # the event itself carries the closest alarm; earlier ones become '[미리 알림]' events
        self.deadline_reminder_minutes = min(self.deadline_reminders)
        self.meeting_reminder_minutes = min(self.meeting_reminders)
        self.precheck_days = sorted(set(p.calendar.precheck_days), reverse=True)
        self.precheck_time = p.calendar.precheck_time
        self.cleanup_mode = p.cleanup.mode
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mail_agent.sqlite3"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite3"

    @property
    def msal_cache_path(self) -> Path:
        return self.data_dir / "msal_token_cache.json"

    def anthropic_client_kwargs(self) -> dict:
        kw: dict = {"api_key": self.anthropic_api_key}
        if self.anthropic_workspace_id:
            kw["default_headers"] = {"anthropic-workspace-id": self.anthropic_workspace_id}
        return kw

    def load_rules(self) -> dict:
        if not self.rules_file.exists():
            return {}
        with self.rules_file.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
