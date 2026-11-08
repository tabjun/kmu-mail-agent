"""Triage API (FastAPI). Serves the Outlook add-in and anything else that wants
"이 메일 얼마나 급해?" as a service.

    python -m mail_agent serve                  # https://localhost:3000 (dev certs) or http
    GET  /api/health
    POST /api/triage            one mail → priority, due, action items, calendar candidates
    GET  /api/items             open items, re-bucketed for *now* (the digest as JSON)
    POST /api/items/{id}/done
    POST /api/items/{id}/feedback   user correction → stored, fed back into the triage prompt
    /addin/*                    static files of the Outlook add-in

Results are shared with the morning batch through the same SQLite store, so a
mail the batch already triaged is answered from cache (no Claude call), matched
by Graph id or by RFC 822 Message-ID.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .calendars import build_spec
from .config import PROJECT_ROOT, Settings
from .graph import build_rules_text
from .llm import ClaudeLLM, RuleBasedLLM
from .models import CATEGORY_LABEL_KO, PRIORITY_LABEL_KO, Email, Priority, TriagedEmail
from .prioritize import finalize, sort_key
from .store import Store

log = logging.getLogger(__name__)
ENTRA_JWKS = "https://login.microsoftonline.com/common/discovery/v2.0/keys"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class TriageRequest(BaseModel):
    item_id: Optional[str] = Field(None, description="Graph/REST message id (Office.js convertToRestId)")
    internet_message_id: Optional[str] = None
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    received_at: datetime
    body: str = ""
    web_link: Optional[str] = None
    force: bool = Field(False, description="ignore cache and ask Claude again")


class CalendarCandidate(BaseModel):
    title: str
    kind: str
    start: datetime
    end: datetime
    all_day: bool
    location: Optional[str]
    body: str
    reminder_minutes: int
    evidence: str


class TriageResponse(BaseModel):
    id: str
    cached: bool
    source: str
    final_priority: Priority
    priority_label: str
    overdue: bool
    due: Optional[datetime]
    category: str
    category_label: str
    title: str
    sender: str
    subject: str
    summary: str
    action_required: bool
    action_items: list[str]
    confidence: float
    reason: str
    done: bool
    calendar: list[CalendarCandidate]


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    agent: Optional[str] = None
    message: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None


class ApproveRequest(BaseModel):
    message_ids: Optional[list[str]] = None  # subset; None = all items in the proposal


class ImapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=4, max_length=200)
    host: Optional[str] = None


class FeedbackRequest(BaseModel):
    priority: Priority
    note: Optional[str] = Field(None, max_length=300)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(settings: Settings | None = None, store: Store | None = None, llm=None,
               start_monitor: bool = False) -> FastAPI:
    s = settings or Settings()
    store = store or Store(s.db_path)
    _llm = {"v": llm}
    _lock = threading.Lock()

    def get_llm():
        with _lock:
            if _llm["v"] is None:
                _llm["v"] = ClaudeLLM(s) if s.anthropic_api_key else RuleBasedLLM()
            return _llm["v"]

    app = FastAPI(title="KMU Mail Agent API", version="0.2.0")

    from . import activity
    from .activity import log_event

    activity.init(s.data_dir / "logs")
    log_event("server_start", auth_mode=s.api_auth_mode, monitor=start_monitor, mail_source=s.mail_source,
              calendar=s.calendar_provider)

    @app.middleware("http")
    async def no_stale_ui(request: Request, call_next):
        import time as _time

        t0 = _time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as e:
            log_event("api_error", method=request.method, path=request.url.path, error=f"{type(e).__name__}: {e}")
            raise
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/log", "/api/status"):
            log_event("api", method=request.method, path=path, status=response.status_code,
                      ms=int((_time.perf_counter() - t0) * 1000))
        if path == "/" or path in ("/setup", "/chat", "/guide") or path.startswith(("/web/", "/addin/")):
            response.headers["Cache-Control"] = "no-cache"
        return response
    app.add_middleware(
        CORSMiddleware, allow_origins=s.api_allowed_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )

    # -- auth ---------------------------------------------------------------
    jwks = {"client": None}

    def require_user(request: Request, authorization: str | None = Header(None),
                     x_api_key: str | None = Header(None)) -> dict:
        if s.api_auth_mode == "entra":
            if not authorization or not authorization.lower().startswith("bearer "):
                raise HTTPException(401, "Bearer token required")
            import jwt  # PyJWT

            token = authorization.split(" ", 1)[1]
            if jwks["client"] is None:
                jwks["client"] = jwt.PyJWKClient(ENTRA_JWKS, cache_keys=True)
            try:
                key = jwks["client"].get_signing_key_from_jwt(token).key
                claims = jwt.decode(token, key, algorithms=["RS256"], audience=s.api_audience,
                                    options={"require": ["exp", "iss", "aud", "tid"]})
            except Exception as e:
                raise HTTPException(401, f"invalid token: {type(e).__name__}") from e
            if not claims["iss"].startswith("https://login.microsoftonline.com/") or not claims["iss"].endswith("/v2.0"):
                raise HTTPException(401, "unexpected issuer")
            if s.api_allowed_tenants and claims["tid"] not in s.api_allowed_tenants:
                raise HTTPException(403, "tenant not allowed")
            return {"user": claims.get("preferred_username") or claims.get("oid"), "tid": claims["tid"]}
        # dev mode
        if s.api_key:
            if x_api_key != s.api_key:
                raise HTTPException(401, "X-API-Key required")
        elif request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "dev mode without API_KEY only accepts localhost")
        return {"user": "dev", "tid": None}

    def now() -> datetime:
        return datetime.now(s.timezone)

    def to_response(item: TriagedEmail, cached: bool) -> TriageResponse:
        t = item.triage
        cal = []
        for ev in t.events:
            spec = build_spec(item.email, ev, s)
            if spec:
                cal.append(CalendarCandidate(title=spec.title, kind=spec.kind, start=spec.start, end=spec.end,
                                             all_day=spec.all_day, location=spec.location, body=spec.body,
                                             reminder_minutes=spec.reminder_minutes, evidence=ev.evidence))
        return TriageResponse(
            id=item.email.id, cached=cached, source=item.source, final_priority=item.final_priority,
            priority_label=PRIORITY_LABEL_KO[item.final_priority], overdue=item.overdue, due=item.due,
            category=t.category.value, category_label=CATEGORY_LABEL_KO[t.category],
            title=t.title or (item.email.subject[:30] if item.email.subject else t.summary[:30]),
            sender=item.email.sender_name or item.email.sender_email, subject=item.email.subject, summary=t.summary,
            action_required=t.action_required, action_items=t.action_items, confidence=t.confidence,
            reason=t.reason, done=store.is_done(item.email.id), calendar=cal,
        )

    def rebucket(item: TriagedEmail) -> TriagedEmail:
        """Stored items were bucketed on the day they arrived; re-bucket for today."""
        if item.user_corrected:
            return item
        fresh = finalize(item.email, item.triage, now(), source=item.source)
        item.final_priority, item.due, item.overdue = fresh.final_priority, fresh.due, fresh.overdue
        return item

    # -- routes -------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"ok": True, "auth_mode": s.api_auth_mode, "model": s.triage_model}

    @app.post("/api/triage", response_model=TriageResponse)
    def triage(req: TriageRequest, user: dict = Depends(require_user)):
        if not req.force:
            hit = store.get_item(req.item_id, req.internet_message_id)
            if hit:
                return to_response(rebucket(hit), cached=True)
        msg_id = req.item_id or req.internet_message_id or f"adhoc:{hash((req.subject, req.received_at))}"
        email = Email(
            id=msg_id, subject=req.subject, sender_name=req.sender_name, sender_email=req.sender_email,
            to=req.to, cc=req.cc, received_at=req.received_at, body=req.body, web_link=req.web_link,
            internet_message_id=req.internet_message_id,
        )
        try:
            result = get_llm().triage(email, now(), build_rules_text(s, store))
        except Exception as e:
            log.exception("triage failed")
            raise HTTPException(502, f"triage failed: {type(e).__name__}") from e
        item = finalize(email, result, now())
        store.save_triaged([item])
        return to_response(item, cached=False)

    @app.get("/api/items", response_model=list[TriageResponse])
    def items(days: int = 14, include_fyi: bool = False, user: dict = Depends(require_user)):
        found = store.recent_items(now() - timedelta(days=days))
        out = [rebucket(i) for i in found]
        if not include_fyi:
            out = [i for i in out if i.final_priority != Priority.FYI]
        return [to_response(i, cached=True) for i in sorted(out, key=sort_key)]

    @app.post("/api/items/{message_id}/done")
    def done(message_id: str, user: dict = Depends(require_user)):
        if not store.mark_done(message_id):
            raise HTTPException(404, "unknown message id")
        return {"ok": True}

    @app.post("/api/items/{message_id}/feedback", response_model=TriageResponse)
    def feedback(message_id: str, req: FeedbackRequest, user: dict = Depends(require_user)):
        item = store.get_item(message_id)
        if not item:
            raise HTTPException(404, "unknown message id")
        store.add_feedback(item, req.priority.value, req.note)
        return to_response(item, cached=True)

    # ======================================================================
    # Setup chatbot + service console (web UI at /)
    # ======================================================================
    from fastapi.responses import FileResponse

    from .actions import execute_action
    from .agents import AGENTS
    from .assistant import Assistant
    from .preferences import MODEL_CHOICES, PreferencesStore
    from .service import Monitor, graph_runtime, run_pipeline

    def fresh_settings() -> Settings:
        # same object identity matters for tests that tweak `s`; copy the attributes we mutate per run
        import copy

        return copy.copy(s)

    monitor = Monitor(fresh_settings)
    assistant = Assistant(fresh_settings, store, monitor)
    prefs_store = PreferencesStore(s.prefs_path)
    device_logins: dict[str, dict] = {}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(PROJECT_ROOT / "web" / "index.html")

    @app.get("/chat", include_in_schema=False)
    def chat_page():
        return FileResponse(PROJECT_ROOT / "web" / "chat.html")

    @app.get("/setup", include_in_schema=False)
    def setup_page():
        return FileResponse(PROJECT_ROOT / "web" / "setup.html")

    @app.get("/guide", include_in_schema=False)
    def guide_page():
        return FileResponse(PROJECT_ROOT / "web" / "guide.html")

    @app.post("/api/log")
    def client_log(body: dict, user: dict = Depends(require_user)):
        """Browser-side events (page views, clicks, JS errors). Only small, non-secret fields are kept."""
        allowed = {k: body.get(k) for k in ("type", "page", "target", "detail", "message", "source", "line")}
        log_event("ui", **{k: v for k, v in allowed.items() if v is not None})
        return {"ok": True}

    @app.get("/api/activity")
    def recent_activity(n: int = 200, user: dict = Depends(require_user)):
        return activity.tail(min(n, 2000))

    @app.get("/api/models")
    def models(user: dict = Depends(require_user)):
        return {"choices": MODEL_CHOICES, "default_chat": prefs_store.load().chat_model}

    @app.get("/api/preferences")
    def get_prefs(user: dict = Depends(require_user)):
        return prefs_store.load()

    @app.put("/api/preferences")
    def put_prefs(patch: dict, user: dict = Depends(require_user)):
        try:
            out = prefs_store.update(patch)
            log_event("prefs_saved", keys=sorted(patch.keys()), source="web")
            return out
        except Exception as e:
            log_event("prefs_save_failed", keys=sorted(patch.keys()), error=str(e)[:300])
            raise HTTPException(400, str(e)) from e

    @app.post("/api/chat")
    def chat(req: ChatRequest, user: dict = Depends(require_user)):
        try:
            if req.agent and req.agent not in AGENTS:
                raise HTTPException(400, "unknown agent")
            import time as _t

            t0 = _t.perf_counter()
            out = assistant.chat(req.session_id, req.message, req.model, req.agent)
            log_event("chat", agent=req.agent, model=out.get("model"), user_text=req.message[:200],
                      reply_chars=len(out.get("reply") or ""), cards=[a.get("type") for a in out.get("attachments", [])],
                      prefs_changed=out.get("preferences_changed"), ms=int((_t.perf_counter() - t0) * 1000))
            return out
        except Exception as e:
            log.exception("chat failed")
            raise HTTPException(502, f"chat failed: {type(e).__name__}: {e}"[:300]) from e

    @app.get("/api/agents")
    def agents(user: dict = Depends(require_user)):
        return [a.public() for a in AGENTS.values()]

    @app.get("/api/agents/{agent_id}/greeting")
    def agent_greeting(agent_id: str, user: dict = Depends(require_user)):
        if agent_id not in AGENTS:
            raise HTTPException(404, "unknown agent")
        return {"agent": AGENTS[agent_id].public(), "text": assistant.greeting(agent_id)}

    @app.get("/api/chat/{session_id}")
    def chat_history(session_id: str, user: dict = Depends(require_user)):
        return {"session_id": session_id, "messages": assistant.history_for_ui(session_id)}

    @app.get("/api/actions")
    def actions(status: Optional[str] = "pending", user: dict = Depends(require_user)):
        return store.list_actions(status or None)

    @app.post("/api/actions/{action_id}/approve")
    def approve(action_id: int, req: ApproveRequest | None = None, user: dict = Depends(require_user)):
        action = store.get_action(action_id)
        if not action:
            raise HTTPException(404, "unknown action")
        st = fresh_settings()
        st.apply_preferences()
        rt = graph_runtime(st, store)
        try:
            only = set(req.message_ids) if req and req.message_ids is not None else None
            return execute_action(action, store, rt.mail_actions, only_ids=only)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/actions/{action_id}/reject")
    def reject(action_id: int, user: dict = Depends(require_user)):
        if not store.get_action(action_id):
            raise HTTPException(404, "unknown action")
        store.set_action_status(action_id, "rejected")
        return {"ok": True}

    @app.get("/api/accounts")
    def accounts(user: dict = Depends(require_user)):
        from .imap_mail import ImapAccountStore

        details = []
        if s.ms_client_id:
            from .msgraph import GraphAuth

            details += [{"username": u, "type": "microsoft", "label": "Microsoft · 메일+캘린더"}
                        for u in GraphAuth(s).accounts()]
        details += [{"username": a["username"], "type": "imap", "label": f"메일 (IMAP · {a['host']})"}
                    for a in ImapAccountStore(s).list()]
        return {"accounts": [d["username"] for d in details], "details": details, "configured": bool(s.ms_client_id)}

    @app.post("/api/accounts/imap")
    def add_imap(req: ImapRequest, user: dict = Depends(require_user)):
        """Gmail / Naver / Daum etc. with an app password (stored in the OS credential store)."""
        from .imap_mail import ImapAccountStore

        try:
            info = ImapAccountStore(s).add(req.username.strip(), req.password.replace(" ", ""), req.host or None)
        except Exception as e:
            log_event("imap_add_failed", username=req.username.strip(), error=str(e)[:300])
            raise HTTPException(400, str(e)[:300]) from e
        log_event("imap_add_done", username=info["username"], host=info["host"], total=info.get("total"))
        return {"ok": True, "username": info["username"], "host": info["host"], "total": info.get("total")}

    @app.get("/api/accounts/imap-help")
    def imap_help(username: str, user: dict = Depends(require_user)):
        from .imap_mail import APP_PASSWORD_HELP, guess_host

        host = guess_host(username)
        return {"host": host, "help": APP_PASSWORD_HELP.get(host or "", "메일 서비스의 IMAP 설정과 앱 비밀번호를 확인하세요.")}

    @app.post("/api/accounts/login")
    def browser_login(body: dict | None = None, user: dict = Depends(require_user)):
        """One-click login: the Microsoft sign-in page opens in this PC's browser; the app finishes the connection."""
        from .msgraph import GraphAuth

        import uuid as _uuid

        expected = ((body or {}).get("expected") or "").strip()
        login_id = _uuid.uuid4().hex[:10]
        device_logins[login_id] = {"status": "waiting", "mode": "browser"}

        def run():
            log_event("account_login_start", mode="browser", expected=expected)
            try:
                username = GraphAuth(s).login_interactive(login_hint=expected or None)
                device_logins[login_id].update(status="done", username=username,
                                               mismatch=bool(expected) and username.lower() != expected.lower())
                log_event("account_login_done", mode="browser", username=username,
                          mismatch=device_logins[login_id]["mismatch"])
            except Exception as e:
                device_logins[login_id].update(status="failed", error=str(e)[:300])
                log_event("account_login_failed", mode="browser", expected=expected, error=str(e)[:300])

        threading.Thread(target=run, daemon=True).start()
        return {"login_id": login_id, **device_logins[login_id]}

    @app.post("/api/accounts/device-login")
    def device_login(body: dict | None = None, user: dict = Depends(require_user)):
        """Starts a Microsoft device-code sign-in; the page shows the code, a thread waits for completion."""
        from .msgraph import SCOPES, GraphAuth

        auth = GraphAuth(s)
        flow = auth.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise HTTPException(502, f"device flow failed: {flow.get('error_description')}")
        login_id = flow["user_code"]
        expected = ((body or {}).get("expected") or "").strip().lower()
        device_logins[login_id] = {"status": "waiting", "user_code": flow["user_code"],
                                   "verification_uri": flow["verification_uri"], "expires_in": flow.get("expires_in")}

        def wait():
            result = auth.app.acquire_token_by_device_flow(flow)
            auth._save()
            if "access_token" in result:
                username = (result.get("id_token_claims") or {}).get("preferred_username") or ""
                device_logins[login_id].update(status="done", username=username,
                                               mismatch=bool(expected) and username.lower() != expected)
            else:
                device_logins[login_id].update(status="failed", error=result.get("error_description", "")[:300])

        threading.Thread(target=wait, daemon=True).start()
        return device_logins[login_id]

    @app.get("/api/accounts/device-login/{login_id}")
    def device_login_status(login_id: str, user: dict = Depends(require_user)):
        if login_id not in device_logins:
            raise HTTPException(404, "unknown login")
        return device_logins[login_id]

    @app.post("/api/accounts/remove")
    def remove_account(body: dict, user: dict = Depends(require_user)):
        from .imap_mail import ImapAccountStore

        username = body.get("username", "")
        if ImapAccountStore(s).remove(username):
            return {"removed": True}
        from .msgraph import GraphAuth

        return {"removed": GraphAuth(s).remove_account(username)}

    run_state: dict = {"running": False, "last": None}

    @app.post("/api/run")
    def run_now(user: dict = Depends(require_user)):
        if run_state["running"]:
            return {"started": False, "reason": "already running"}

        def job():
            run_state["running"] = True
            log_event("run_start", trigger="manual")
            try:
                st = fresh_settings()
                st.apply_preferences()
                out = run_pipeline(st)
                log_event("run_done", trigger="manual", new=len(out.get("triaged", [])),
                          calendar_created=len(out.get("calendar_created", [])),
                          needs_review=len(out.get("needs_review", [])),
                          cleanup=(out.get("cleanup") or {}).get("count"),
                          checklist=(out.get("checklist") or {}).get("title"), errors=out.get("errors", []))
                run_state["last"] = {"ok": True, "at": datetime.now(st.timezone).isoformat(timespec="minutes"),
                                     "new": len(out.get("triaged", [])),
                                     "calendar_created": len(out.get("calendar_created", [])),
                                     "checklist": out.get("checklist"), "cleanup": out.get("cleanup"),
                                     "errors": out.get("errors", []), "digest": out.get("digest_text", "")}
            except Exception as e:
                run_state["last"] = {"ok": False, "error": str(e)[:500]}
                log_event("run_failed", trigger="manual", error=f"{type(e).__name__}: {str(e)[:400]}")
            finally:
                run_state["running"] = False

        threading.Thread(target=job, daemon=True).start()
        return {"started": True}

    @app.post("/api/calendar/test-alarm")
    def test_alarm(user: dict = Depends(require_user)):
        """Puts a short event 2 minutes from now with an alarm at its start, to check the phone/PC notification."""
        from .calendars import OutlookCalendar, build_test_alarm_spec

        st = fresh_settings()
        st.apply_preferences()
        rt = graph_runtime(st, store)
        if rt.calendar_client is None:
            raise HTTPException(400, "일정을 넣을 Microsoft 계정이 없습니다. '+ 계정 추가'에서 Outlook 계정을 연결하세요.")
        spec = build_test_alarm_spec(st, datetime.now(st.timezone))
        try:
            event_id = OutlookCalendar(rt.calendar_client, st).create(spec)
        except Exception as e:
            log_event("test_alarm_failed", error=str(e)[:300])
            raise HTTPException(502, f"테스트 알림을 넣지 못했습니다: {str(e)[:200]}") from e
        log_event("test_alarm", account=rt.calendar_client.username, at=spec.start.isoformat(timespec="minutes"))
        return {"ok": True, "account": rt.calendar_client.username, "at": spec.start.strftime("%H:%M"), "event_id": event_id}

    @app.get("/api/health/full")
    def health_full(user: dict = Depends(require_user)):
        """Plain connection checklist: service running? Claude? Outlook mail? Outlook calendar (+ registered events)?"""
        import anthropic

        st = fresh_settings()
        prefs = st.apply_preferences()
        checks: list[dict] = []

        def add(key, label, ok, detail, level=None, links=None, action=None):
            checks.append({"key": key, "label": label, "status": level or ("ok" if ok else "fail"),
                           "detail": detail, "links": links or [], "action": action})

        # 1) background service
        auto = start_monitor and prefs.monitor.enabled and prefs.setup_completed
        add("service", "서비스 실행", True,
            f"백그라운드에서 {prefs.monitor.interval_minutes}분마다 메일 확인" if auto
            else "실행 중 · 자동 확인 꺼짐 (서비스로 설치하면 켜집니다)", None if auto else "warn")

        # 2) Claude API
        try:
            anthropic.Anthropic(**st.anthropic_client_kwargs(), max_retries=1, timeout=20).models.retrieve(st.triage_model)
            add("claude", "Claude AI 연결", True, f"모델 {MODEL_CHOICES.get(st.triage_model, st.triage_model)}")
        except Exception as e:
            add("claude", "Claude AI 연결", False, f"연결 실패: {type(e).__name__} — .env의 ROOT_API_KEY를 확인하세요")

        # 3) + 4) Outlook mail / calendar per account
        rt = graph_runtime(st, store)
        if st.mail_source == "sample":
            add("mail", "메일 연결", False, "데모 모드(샘플 메일)로 실행 중입니다", "warn")
        elif not rt.mail_clients and not rt.imap_clients:
            add("mail", "메일 연결", False, "연결된 메일 계정이 없습니다 → '+ 계정 추가'")
        for ic in (rt.imap_clients or []) if st.mail_source != "sample" else []:
            try:
                info = ic.check()
                add("mail", "메일 연결 (IMAP)", True,
                    f"{ic.username} · 받은편지함 {info['total']}통 (안 읽음 {info['unread']})")
            except Exception as e:
                add("mail", "메일 연결 (IMAP)", False, f"{ic.username} · {str(e)[:120]}")
        for c in rt.mail_clients if st.mail_source != "sample" else []:
            try:
                inbox = c.get("/me/mailFolders/inbox", params={"$select": "totalItemCount,unreadItemCount"})
                add("mail", "Outlook 메일 연결", True,
                    f"{c.username} · 받은편지함 {inbox.get('totalItemCount', 0)}통 (안 읽음 {inbox.get('unreadItemCount', 0)})")
            except Exception as e:
                log.warning("mail health failed for %s: %s", c.username, e)
                add("mail", "Outlook 메일 연결", False, f"{c.username} · 연결이 끊겼습니다. '다시 연결'을 눌러 주세요",
                    action={"type": "reconnect", "account": c.username})

        if st.calendar_provider != "outlook":
            add("calendar", "Outlook 캘린더 연결", False,
                f"캘린더 방식이 '{st.calendar_provider}'입니다. Outlook 캘린더에 넣으려면 CALENDAR_PROVIDER=outlook", "warn")
        elif rt.calendar_client is None:
            add("calendar", "Outlook 캘린더 연결", False, "일정을 넣을 계정이 없습니다")
        else:
            c = rt.calendar_client
            try:
                cal = c.get("/me/calendar", params={"$select": "name,canEdit"})
                now_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                ev = c.get("/me/events", params={
                    "$filter": f"categories/any(x:x eq 'MailAgent') and start/dateTime ge '{now_utc}'",
                    "$select": "subject,start,webLink", "$top": "50"},
                    headers={"Prefer": 'outlook.timezone="Korea Standard Time"'}).get("value", [])
                ev.sort(key=lambda e: e["start"]["dateTime"])
                links = [{"title": e["subject"], "when": e["start"]["dateTime"][5:16].replace("T", " "),
                          "url": e.get("webLink")} for e in ev[:3]]
                add("calendar", "Outlook 캘린더 연결", bool(cal.get("canEdit")),
                    f"{c.username} · '{cal.get('name')}' 캘린더 · 앞으로 등록된 일정 {len(ev)}건"
                    if cal.get("canEdit") else f"{c.username} · 캘린더에 쓸 권한이 없습니다", links=links)
            except Exception as e:
                log.warning("calendar health failed for %s: %s", c.username, e)
                add("calendar", "Outlook 캘린더 연결", False, f"{c.username} · 연결이 끊겼습니다. '다시 연결'을 눌러 주세요",
                    action={"type": "reconnect", "account": c.username})

        order = {"fail": 2, "warn": 1, "ok": 0}
        worst = max((order[c["status"]] for c in checks), default=0)
        log_event("health", overall=["ok", "warn", "fail"][worst],
                  checks=[f"{c['label']}={c['status']}" for c in checks])
        return {"overall": ["ok", "warn", "fail"][worst], "checks": checks,
                "checked_at": datetime.now(st.timezone).isoformat(timespec="minutes")}

    @app.get("/api/status")
    def status(user: dict = Depends(require_user)):
        prefs = prefs_store.load()
        return {"monitor": {"enabled": prefs.monitor.enabled, "interval_minutes": prefs.monitor.interval_minutes,
                            "running_in_server": start_monitor, "last_run": monitor.last_run},
                "manual_run": run_state, "pending_actions": len(store.list_actions("pending")),
                "setup_completed": prefs.setup_completed}

    if start_monitor:
        monitor.start()

    web_dir = PROJECT_ROOT / "web"
    if web_dir.exists():
        app.mount("/web", StaticFiles(directory=web_dir), name="web")

    addin_dir = PROJECT_ROOT / "addin"
    if addin_dir.exists():
        app.mount("/addin", StaticFiles(directory=addin_dir, html=True), name="addin")
    return app
