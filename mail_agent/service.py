"""Shared runtime: builds the pipeline dependencies once for the CLI, the web
server's background monitor, the setup chatbot and the MCP server."""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Settings
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    store: Store
    auth: object | None            # msgraph.GraphAuth
    mail_clients: list             # msgraph.GraphClient per selected account
    calendar_client: object | None # msgraph.GraphClient that owns the calendar
    mail_actions: object | None    # msgraph.MailActions (Graph + IMAP accounts)
    imap_clients: list | None = None

    def mail_source(self):
        """Every connected mailbox — Microsoft (Graph) and IMAP (Gmail, Naver …) — as one source."""
        from .imap_mail import CombinedMailSource, ImapMailSource
        from .msgraph import GraphMailSource

        sources = []
        if self.mail_clients:
            sources.append(GraphMailSource(self.mail_clients, self.settings))
        if self.imap_clients:
            sources.append(ImapMailSource(self.imap_clients, self.settings))
        return CombinedMailSource(sources) if sources else None


def graph_runtime(settings: Settings, store: Store | None = None, need_graph: bool = True) -> Runtime:
    store = store or Store(settings.db_path)
    if not need_graph:
        return Runtime(settings, store, None, [], None, None)
    from .imap_mail import ImapAccountStore
    from .msgraph import GraphAuth, GraphClient, MailActions

    wanted = [a.lower() for a in settings.mail_accounts]
    pick = (lambda names: names) if "all" in wanted else (lambda names: [n for n in names if n.lower() in wanted])

    auth, clients, cal = None, [], None
    if settings.ms_client_id:
        auth = GraphAuth(settings)
        signed_in = auth.accounts()
        selected = pick(signed_in)
        clients = [GraphClient(auth, u) for u in selected]
        # the calendar always lives in a Microsoft account (Outlook), even when mail comes from Gmail
        owner = settings.calendar_account if settings.calendar_account in signed_in else \
            (selected[0] if selected else (signed_in[0] if signed_in else None))
        cal = GraphClient(auth, owner) if owner else None
    imap = [c for c in ImapAccountStore(settings).clients() if c.username in pick([c.username])]
    actions = MailActions(clients + imap) if (clients or imap) else None
    return Runtime(settings, store, auth, clients, cal, actions, imap)


def build_calendar(settings: Settings, rt: Runtime):
    """(calendar sink, main calendar account). Events follow the mailbox they came from:
    Office 365/Outlook → same account's Outlook calendar, Gmail → same Gmail's Google Calendar (if connected).
    The daily checklist and anything unmatched go to the main calendar (preferences.calendar_account)."""
    from .calendars import OutlookCalendar, RoutingCalendar, make_calendar, route_key

    if settings.calendar_provider.lower() != "outlook":  # ics / none / legacy google
        return make_calendar(settings, rt.calendar_client), getattr(rt.calendar_client, "username", None)
    from .google_cal import GoogleAuth, GoogleCalendar

    gauth = GoogleAuth(settings)
    google: dict = {}

    def g(email: str):
        if email.lower() not in google:
            try:
                google[email.lower()] = GoogleCalendar(settings, email, gauth)
            except Exception as e:  # expired token etc. → fall back to the main calendar
                log.warning("Google calendar %s unavailable: %s", email, e)
                google[email.lower()] = None
        return google[email.lower()]

    by_account = {}
    for c in rt.mail_clients or []:
        by_account[route_key("", c.username)] = OutlookCalendar(c, settings)
    for ic in rt.imap_clients or []:
        if gauth.has(ic.username) and g(ic.username):
            by_account[route_key("imap:", ic.username)] = g(ic.username)

    main, wanted = None, settings.calendar_account or ""
    if wanted.startswith("google:") and gauth.has(wanted[7:]):
        main = g(wanted[7:])
    if main is None and rt.calendar_client is not None:
        main = OutlookCalendar(rt.calendar_client, settings)
    if main is None:
        main = next((x for x in (g(e) for e in gauth.accounts()) if x), None)
    if main is None and not by_account:
        return None, None
    return RoutingCalendar(main, by_account), getattr(main, "username", None)


_run_lock = threading.Lock()


def run_pipeline(settings: Settings, *, source=None, llm=None, store: Store | None = None,
                 now: datetime | None = None, interactive: bool = False) -> dict:
    """One full pass: fetch → triage → cleanup proposal → calendar → checklist → digest → notify."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from .graph import Deps, build_graph, initial_state
    from .llm import ClaudeLLM
    from .msgraph import GraphMailSource
    from .notifiers import make_notifiers

    with _run_lock:  # the monitor and a chat-triggered run must not overlap
        if source is None and settings.mail_source == "sample":
            from .config import PROJECT_ROOT
            from .sources import SampleMailSource

            source = SampleMailSource(PROJECT_ROOT / "samples" / "emails.json")
            if settings.cleanup_mode == "auto":
                settings.cleanup_mode = "ask"  # never try to move sample ids in a real mailbox
        rt = graph_runtime(settings, store, need_graph=source is None or settings.calendar_provider == "outlook")
        if source is None:
            source = rt.mail_source()
            if source is None:
                raise RuntimeError("연결된 메일 계정이 없습니다. 대시보드의 '+ 계정 추가'에서 연결하세요.")
        calendar, calendar_owner = build_calendar(settings, rt)
        deps = Deps(settings=settings, llm=llm or ClaudeLLM(settings), store=rt.store, source=source,
                    calendar=calendar, notifiers=make_notifiers(settings, rt.calendar_client),
                    calendar_owner=calendar_owner, mail_actions=rt.mail_actions)
        now = now or datetime.now(settings.timezone)
        conn = sqlite3.connect(settings.checkpoint_path, check_same_thread=False)
        try:
            app = build_graph(deps, checkpointer=SqliteSaver(conn))
            config = {"configurable": {"thread_id": f"run-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"},
                      "max_concurrency": settings.triage_concurrency}
            result = app.invoke(initial_state(now, interactive=interactive), config)
            result["_app"], result["_config"] = app, config
            return result
        finally:
            if not interactive:
                conn.close()


class Monitor:
    """Background mailbox watcher for `python -m mail_agent serve`."""

    def __init__(self, settings_factory):
        self.settings_factory = settings_factory
        self._stop = threading.Event()
        self.last_run: dict = {}
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="mail-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            settings = self.settings_factory()
            prefs = settings.apply_preferences()
            interval = max(5, prefs.monitor.interval_minutes) * 60
            if prefs.monitor.enabled and prefs.setup_completed:
                from .activity import log_event

                log_event("run_start", trigger="monitor")
                try:
                    out = run_pipeline(settings)
                    log_event("run_done", trigger="monitor", new=len(out.get("triaged", [])),
                              calendar_created=len(out.get("calendar_created", [])),
                              cleanup=(out.get("cleanup") or {}).get("count"), errors=out.get("errors", []))
                    self.last_run = {"at": datetime.now(settings.timezone).isoformat(timespec="minutes"),
                                     "new": len(out.get("triaged", [])), "errors": out.get("errors", [])}
                except Exception as e:
                    log.exception("monitor run failed")
                    log_event("run_failed", trigger="monitor", error=f"{type(e).__name__}: {str(e)[:400]}")
                    self.last_run = {"at": datetime.now(settings.timezone).isoformat(timespec="minutes"),
                                     "error": str(e)[:300]}
            self._stop.wait(interval)
