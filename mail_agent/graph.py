"""The LangGraph workflow.

    START → fetch_mail → prefilter ─┬─(Send ×N, parallel)→ triage_one ─┐
                                    └──────────(nothing new)───────────┤
                                                                       ▼
          prioritize → plan_calendar → [review_calendar (interrupt, --interactive)]
                     → sync_calendar → compose_digest → notify → finalize → END

Deterministic steps (dedupe, date bucketing, idempotent calendar writes,
rendering) are plain Python; Claude is used only where judgment is needed
(per-mail triage, digest headline). Every LLM failure degrades to a visible
"확인 필요" item instead of silently dropping a mail.
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .actions import execute_action
from .calendars import CalendarSink, build_checklist_spec, build_precheck_specs, build_spec, event_specs
from .config import Settings
from .digest import fmt_due, items_for_llm, render_html, render_text
from .llm import LLM
from .models import Category, Email, Priority, TriagedEmail, TriageResult
from .notifiers import Notifier
from .prioritize import finalize, sort_key
from .sources import MailSource
from .store import Store

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State (JSON-friendly dicts so the SQLite checkpointer can persist it)
# ---------------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    now: str
    interactive: bool
    emails: list[dict]                                   # fetched, not yet processed
    to_triage: list[dict]
    triaged: Annotated[list[dict], operator.add]         # TriagedEmail dumps (fan-in)
    errors: Annotated[list[str], operator.add]
    digest_items: list[dict]                             # new + carried-over, sorted
    calendar_plan: list[dict]                            # auto-create
    needs_review: list[dict]                             # low confidence
    calendar_created: list[dict]
    cleanup: dict                                        # proposal / auto-cleanup result
    checklist: dict                                      # daily to-do event (title, when)
    digest_text: str
    digest_html: str
    notified: list[str]


class TriageTask(TypedDict):
    email: dict
    now: str


@dataclass
class Deps:
    settings: Settings
    llm: LLM
    store: Store
    source: MailSource
    calendar: CalendarSink | None = None
    notifiers: list[Notifier] = field(default_factory=list)
    calendar_owner: str | None = None   # account whose calendar receives events
    mail_actions: object | None = None  # msgraph.MailActions (move to Deleted Items)


# ---------------------------------------------------------------------------
# Rules (config/rules.yaml) → prompt text + cheap pre-filter
# ---------------------------------------------------------------------------
def rules_to_text(rules: dict) -> str:
    lines: list[str] = []
    owner = rules.get("owner") or {}
    if owner:
        lines.append(f"- 메일함 주인: {owner.get('name', '')} ({owner.get('role', '')}, {owner.get('affiliation', '')})")
    for v in rules.get("vip_senders") or []:
        lines.append(f"- 중요 발신자: '{v.get('pattern')}' = {v.get('note', '')}. 이 발신자의 요청은 놓치지 않도록 action_required와 마감 추출을 특히 꼼꼼히 한다.")
    for h in rules.get("priority_hints") or []:
        lines.append(f"- {h}")
    return "\n".join(lines)


def feedback_to_text(feedback: list[dict]) -> str:
    """User corrections from the Outlook add-in, fed back as few-shot hints."""
    if not feedback:
        return ""
    lines = ["", "## 사용자가 직접 고친 분류 (비슷한 메일은 이 판단을 따른다)"]
    for f in feedback:
        note = f" — 이유: {f['note']}" if f.get("note") else ""
        lines.append(f"- '{f['subject']}' ({f['sender']}): {f['model_priority']} → {f['corrected_priority']}{note}")
    return "\n".join(lines)


def build_rules_text(settings: Settings, store: Store) -> str:
    from .preferences import PreferencesStore

    prefs = PreferencesStore(settings.prefs_path).load()
    return "\n".join(filter(None, [
        prefs.to_prompt_rules(), rules_to_text(settings.load_rules()), feedback_to_text(store.recent_feedback()),
    ]))


def is_skipped(email: Email, rules: dict) -> bool:
    skip = rules.get("skip") or {}
    subj = email.subject or ""
    sender = (email.sender_email or "").lower()
    return any(s in subj for s in skip.get("subject_contains") or []) or \
        any(s.lower() in sender for s in skip.get("sender_contains") or [])


def _when_label(spec, now: datetime) -> str:
    if spec.all_day:
        return fmt_due(spec.start, now).split(" ")[0] + " 종일"
    return fmt_due(spec.end if spec.kind == "deadline" else spec.start, now)


def _fallback(email: Email, reason: str) -> TriageResult:
    return TriageResult(
        summary=f"(자동 분류 실패) {email.subject[:80]}", category=Category.OTHER, priority=Priority.TODAY,
        action_required=True, action_items=["원문 직접 확인"], deadline=None, events=[], confidence=0.0,
        reason=reason[:200],
    )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph(deps: Deps, checkpointer=None):
    s = deps.settings
    rules = s.load_rules()
    rules_text = build_rules_text(s, deps.store)
    now_of = lambda state: datetime.fromisoformat(state["now"])

    def fetch_mail(state: AgentState) -> dict:
        now = now_of(state)
        last = deps.store.get_meta(f"last_fetch:{deps.source.name}")
        if last:  # overlap 1h so late-arriving mail is not missed; dedupe handles repeats
            since = min(now - timedelta(hours=s.lookback_hours), datetime.fromisoformat(last) - timedelta(hours=1))
        else:     # first run: backfill a week so open deadlines are picked up
            since = now - timedelta(days=s.first_run_lookback_days)
        emails = deps.source.fetch(since)
        done = deps.store.processed_ids([e.id for e in emails])
        fresh = [e for e in emails if e.id not in done]
        log.info("fetched %d mails, %d new", len(emails), len(fresh))
        return {"emails": [e.model_dump(mode="json") for e in fresh]}

    def prefilter(state: AgentState) -> dict:
        now = now_of(state)
        to_triage, skipped = [], []
        for raw in state.get("emails", []):
            email = Email.model_validate(raw)
            if is_skipped(email, rules):
                t = TriageResult(summary=email.subject[:80], category=Category.NEWSLETTER, priority=Priority.FYI,
                                 action_required=False, action_items=[], deadline=None, events=[],
                                 confidence=1.0, reason="skip 규칙(rules.yaml)",
                                 cleanup_candidate=True, cleanup_reason="제목에 광고 표시가 있는 메일")
                skipped.append(finalize(email, t, now, source="rule").model_dump(mode="json"))
            else:
                to_triage.append(raw)
        return {"to_triage": to_triage, "triaged": skipped}

    def fan_out(state: AgentState):
        tasks = state.get("to_triage", [])
        if not tasks:
            return "prioritize"
        return [Send("triage_one", {"email": e, "now": state["now"]}) for e in tasks]

    def triage_one(task: TriageTask) -> dict:
        email = Email.model_validate(task["email"])
        now = datetime.fromisoformat(task["now"])
        try:
            result = deps.llm.triage(email, now, rules_text)
            item = finalize(email, result, now, source="llm")
            return {"triaged": [item.model_dump(mode="json")]}
        except Exception as e:  # never drop a mail: surface it for manual check
            log.exception("triage failed for %s", email.id)
            item = finalize(email, _fallback(email, f"{type(e).__name__}: {e}"), now, source="fallback")
            return {"triaged": [item.model_dump(mode="json")], "errors": [f"{email.subject[:40]}: {type(e).__name__}"]}

    def prioritize(state: AgentState) -> dict:
        now = now_of(state)
        new_items = [TriagedEmail.model_validate(d) for d in state.get("triaged", [])]
        # Persist right away so a later failure never re-bills Claude for these mails.
        deps.store.save_triaged([i for i in new_items if i.source != "fallback"])
        carried = deps.store.open_items({i.email.id for i in new_items}, not_before=now - timedelta(days=3))
        for it in carried:  # re-bucket yesterday's items against today's date
            if it.user_corrected:
                continue
            refreshed = finalize(it.email, it.triage, now, source=it.source)
            it.final_priority, it.due, it.overdue = refreshed.final_priority, refreshed.due, refreshed.overdue
        items = sorted(new_items + carried, key=sort_key)
        return {"digest_items": [i.model_dump(mode="json") for i in items]}

    def plan_cleanup(state: AgentState) -> dict:
        """Unnecessary mail → one pending proposal ('이런 메일 N통 왔는데 지울까요?') or auto-move."""
        if s.cleanup_mode == "off":
            return {"cleanup": {}}
        already = deps.store.pending_message_ids()
        items = []
        for d in state.get("digest_items", []):
            it = TriagedEmail.model_validate(d)
            if it.carried_over or not it.triage.cleanup_candidate or it.email.id in already:
                continue
            items.append({"message_id": it.email.id, "account": it.email.account, "subject": it.email.subject,
                          "sender": it.email.sender_name or it.email.sender_email,
                          "summary": it.triage.summary, "reason": it.triage.cleanup_reason or it.triage.reason})
        if not items:
            return {"cleanup": {}}
        title = f"불필요한 메일 {len(items)}통 정리"
        if s.cleanup_mode == "auto" and deps.mail_actions is not None and not s.dry_run:
            action_id = deps.store.add_action("delete_mail", title, items, status="approved")
            result = execute_action(deps.store.get_action(action_id), deps.store, deps.mail_actions)
            return {"cleanup": {"mode": "auto", "action_id": action_id, "count": len(items), "result": result}}
        action_id = deps.store.add_action("delete_mail", title, items)
        return {"cleanup": {"mode": "ask", "action_id": action_id, "count": len(items),
                            "items": [{"subject": i["subject"], "reason": i["reason"]} for i in items]}}

    def plan_calendar(state: AgentState) -> dict:
        plan, review = [], []
        for d in state.get("digest_items", []):
            it = TriagedEmail.model_validate(d)
            if it.carried_over or it.source != "llm":
                continue
            for ev in it.triage.events:
                auto = it.triage.confidence >= s.auto_calendar_confidence and ev.kind in ("deadline", "meeting")
                for sp in event_specs(it.email, ev, s, now_of(state)):
                    if deps.store.calendar_event_exists(sp.key):
                        continue
                    (plan if auto else review).append({
                        "key": sp.key, "message_id": it.email.id, "event": ev.model_dump(mode="json"),
                        "title": sp.title, "when": _when_label(sp, now_of(state)), "evidence": ev.evidence,
                        "extra": sp.kind in ("precheck", "reminder")})
        return {"calendar_plan": plan, "needs_review": review}

    def route_review(state: AgentState) -> str:
        return "review_calendar" if state.get("interactive") and state.get("needs_review") else "sync_calendar"

    def review_calendar(state: AgentState) -> dict:
        # Human-in-the-loop: pauses the graph; the CLI resumes with Command(resume=[approved keys]).
        approved = interrupt({"needs_review": state["needs_review"]})
        approved = set(approved or [])
        keep_plan = state.get("calendar_plan", []) + [r for r in state["needs_review"] if r["key"] in approved]
        return {"calendar_plan": keep_plan, "needs_review": [r for r in state["needs_review"] if r["key"] not in approved]}

    def sync_calendar(state: AgentState) -> dict:
        created = []
        if deps.calendar is None or s.dry_run:
            return {"calendar_created": [dict(p, dry_run=True) for p in state.get("calendar_plan", [])] if s.dry_run else []}
        items = {d["email"]["id"]: TriagedEmail.model_validate(d) for d in state.get("digest_items", [])}
        for p in state.get("calendar_plan", []):
            it = items[p["message_id"]]
            ev = next(e for e in it.triage.events if e.model_dump(mode="json") == p["event"])
            spec = next((x for x in event_specs(it.email, ev, s, now_of(state)) if x.key == p["key"]), None)
            if spec is None:  # e.g. an alarm time that has passed since planning
                continue
            try:
                event_id = deps.calendar.create(spec)
                deps.store.save_calendar_event(spec.key, it.email.id, deps.calendar.name, event_id, spec.title,
                                               spec.start.isoformat())
                created.append(p)
            except Exception as e:
                log.exception("calendar create failed")
                return {"calendar_created": created, "errors": [f"캘린더 등록 실패: {spec.title}: {e}"]}
        return {"calendar_created": created}

    def sync_checklist(state: AgentState) -> dict:
        """One '오늘 할 일' event per day with the prioritized checklist; reminder at start → phone alarm."""
        now = now_of(state)
        items = [TriagedEmail.model_validate(d) for d in state.get("digest_items", [])]
        spec = build_checklist_spec(items, now, s, deps.calendar_owner)
        if spec is None:
            return {"checklist": {}}
        info = {"title": spec.title, "when": fmt_due(spec.start, now), "lines": spec.body.count("☐")}
        if deps.calendar is None or s.dry_run:
            return {"checklist": dict(info, dry_run=True, body=spec.body)}
        try:
            existing = deps.store.calendar_event_id(spec.key)
            if existing:
                deps.calendar.update(spec, existing)
                info["updated"] = True
            else:
                event_id = deps.calendar.create(spec)
                deps.store.save_calendar_event(spec.key, spec.message_id, deps.calendar.name, event_id,
                                               spec.title, spec.start.isoformat())
        except Exception as e:
            log.exception("checklist event failed")
            return {"checklist": info, "errors": [f"체크리스트 일정 등록 실패: {e}"]}
        return {"checklist": info}

    def compose_digest(state: AgentState) -> dict:
        now = now_of(state)
        items = [TriagedEmail.model_validate(d) for d in state.get("digest_items", [])]
        headline = None
        try:
            headline = deps.llm.digest_headline(items_for_llm(items, now), now)
        except Exception as e:  # the deterministic digest below is still complete
            log.warning("digest headline failed: %s", e)
        usage = deps.llm.usage_summary()
        args = (items, now, headline, state.get("calendar_created", []), state.get("needs_review", []), usage)
        text = render_text(*args)
        cu = state.get("cleanup") or {}
        if cu.get("mode") == "ask":
            text += f"\n\n🧹 정리 제안 {cu['count']}통 — 웹 화면(정리 제안)에서 승인하면 지운 편지함으로 옮깁니다:"
            text += "".join(f"\n  - {i['subject'][:60]} ({i['reason'][:40]})" for i in cu["items"][:10])
        elif cu.get("mode") == "auto":
            text += f"\n\n🧹 규칙에 따라 {cu['count']}통을 지운 편지함으로 옮겼습니다."
        cl = state.get("checklist") or {}
        if cl:
            mode = " (dry-run: 등록 안 함)" if cl.get("dry_run") else (" (갱신)" if cl.get("updated") else "")
            text += f"\n⏰ 캘린더 체크리스트: {cl['title']} · {cl['when']} 알림{mode}"
        if state.get("errors"):
            text += "\n\n⚠ 처리 중 오류: " + "; ".join(state["errors"])
        return {"digest_text": text, "digest_html": render_html(*args)}

    def notify(state: AgentState) -> dict:
        now = now_of(state)
        urgent = sum(1 for d in state.get("digest_items", []) if d["final_priority"] in ("urgent", "today"))
        subject = f"[메일 브리핑] {now.month}/{now.day} 긴급·오늘 {urgent}건"
        results = []
        for n in deps.notifiers:
            try:
                results.append(f"{n.name}: {n.send(subject, state['digest_text'], state['digest_html'], now)}")
            except Exception as e:
                log.exception("notifier %s failed", n.name)
                results.append(f"{n.name}: FAILED {e}")
        return {"notified": results}

    def finalize_run(state: AgentState) -> dict:
        deps.store.set_meta(f"last_fetch:{deps.source.name}", state["now"])
        deps.store.set_meta("last_usage", deps.llm.usage_summary())
        return {}

    g = StateGraph(AgentState)
    g.add_node("fetch_mail", fetch_mail)
    g.add_node("prefilter", prefilter)
    g.add_node("triage_one", triage_one)
    g.add_node("prioritize", prioritize)
    g.add_node("plan_cleanup", plan_cleanup)
    g.add_node("plan_calendar", plan_calendar)
    g.add_node("review_calendar", review_calendar)
    g.add_node("sync_calendar", sync_calendar)
    g.add_node("sync_checklist", sync_checklist)
    g.add_node("compose_digest", compose_digest)
    g.add_node("notify", notify)
    g.add_node("finalize", finalize_run)

    g.add_edge(START, "fetch_mail")
    g.add_edge("fetch_mail", "prefilter")
    g.add_conditional_edges("prefilter", fan_out, ["triage_one", "prioritize"])
    g.add_edge("triage_one", "prioritize")
    g.add_edge("prioritize", "plan_cleanup")
    g.add_edge("plan_cleanup", "plan_calendar")
    g.add_conditional_edges("plan_calendar", route_review, ["review_calendar", "sync_calendar"])
    g.add_edge("review_calendar", "sync_calendar")
    g.add_edge("sync_calendar", "sync_checklist")
    g.add_edge("sync_checklist", "compose_digest")
    g.add_edge("compose_digest", "notify")
    g.add_edge("notify", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def initial_state(now: datetime, interactive: bool = False) -> dict[str, Any]:
    return {"now": now.isoformat(), "interactive": interactive, "triaged": [], "errors": []}
