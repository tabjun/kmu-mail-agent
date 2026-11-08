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
    mail_actions: object | None    # msgraph.MailActions


def graph_runtime(settings: Settings, store: Store | None = None, need_graph: bool = True) -> Runtime:
    store = store or Store(settings.db_path)
    if not need_graph or not settings.ms_client_id:
        return Runtime(settings, store, None, [], None, None)
    from .msgraph import GraphAuth, GraphClient, MailActions

    auth = GraphAuth(settings)
    signed_in = auth.accounts()
    wanted = [a.lower() for a in settings.mail_accounts]
    selected = signed_in if "all" in wanted else [a for a in signed_in if a.lower() in wanted]
    clients = [GraphClient(auth, u) for u in selected]
    owner = settings.calendar_account if settings.calendar_account in signed_in else (selected[0] if selected else None)
    cal = GraphClient(auth, owner) if owner else None
    return Runtime(settings, store, auth, clients, cal, MailActions(clients) if clients else None)


_run_lock = threading.Lock()


def run_pipeline(settings: Settings, *, source=None, llm=None, store: Store | None = None,
                 now: datetime | None = None, interactive: bool = False) -> dict:
    """One full pass: fetch → triage → cleanup proposal → calendar → checklist → digest → notify."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from .calendars import make_calendar
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
            if not rt.mail_clients:
                raise RuntimeError("연결된 Microsoft 계정이 없습니다. 웹 화면의 '계정'에서 계정을 추가하세요.")
            source = GraphMailSource(rt.mail_clients, settings)
        deps = Deps(settings=settings, llm=llm or ClaudeLLM(settings), store=rt.store, source=source,
                    calendar=make_calendar(settings, rt.calendar_client),
                    notifiers=make_notifiers(settings, rt.calendar_client),
                    calendar_owner=getattr(rt.calendar_client, "username", None), mail_actions=rt.mail_actions)
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
                try:
                    out = run_pipeline(settings)
                    self.last_run = {"at": datetime.now(settings.timezone).isoformat(timespec="minutes"),
                                     "new": len(out.get("triaged", [])), "errors": out.get("errors", [])}
                except Exception as e:
                    log.exception("monitor run failed")
                    self.last_run = {"at": datetime.now(settings.timezone).isoformat(timespec="minutes"),
                                     "error": str(e)[:300]}
            self._stop.wait(interval)
