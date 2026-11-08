"""Offline tests: no Claude, no Microsoft. A scripted LLM returns fixed triage."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from mail_agent.calendars import IcsCalendar
from mail_agent.config import PROJECT_ROOT, Settings
from mail_agent.graph import Deps, build_graph, initial_state
from mail_agent.llm import DigestHeadline
from mail_agent.models import Category, Email, ExtractedEvent, Priority, TriageResult
from mail_agent.prioritize import date_priority, finalize
from mail_agent.sources import SampleMailSource
from mail_agent.store import Store

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 9, 7, 30, tzinfo=KST)  # Monday morning


def tr(priority=Priority.THIS_WEEK, action=True, deadline=None, events=(), confidence=0.9, cat=Category.ADMIN):
    return TriageResult(summary="s", category=cat, priority=priority, action_required=action,
                        action_items=["x"] if action else [], deadline=deadline, events=list(events),
                        confidence=confidence, reason="r")


class ScriptedLLM:
    def __init__(self, script: dict[str, TriageResult], fail: set[str] = frozenset()):
        self.script, self.fail, self.calls = script, fail, []

    def triage(self, email, now, rules_text):
        self.calls.append(email.id)
        if email.id in self.fail:
            raise RuntimeError("boom")
        return self.script.get(email.id, tr(Priority.FYI, action=False))

    def digest_headline(self, items_text, now):
        return DigestHeadline(headline="테스트", bullets=["a"])

    def usage_summary(self):
        return {"estimated_usd": 0.0}


@pytest.fixture
def settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    s = Settings()
    s.data_dir, s.output_dir = tmp_path / "data", tmp_path / "out"
    s.data_dir.mkdir(exist_ok=True); s.output_dir.mkdir(exist_ok=True)
    s.notifiers, s.calendar_provider = [], "ics"
    return s


def make_deps(settings, llm):
    return Deps(settings=settings, llm=llm, store=Store(settings.data_dir / "t.sqlite3"),
                source=SampleMailSource(PROJECT_ROOT / "samples" / "emails.json"),
                calendar=IcsCalendar(settings), notifiers=[])


# --- pure functions ------------------------------------------------------------
@pytest.mark.parametrize("due,expected,overdue", [
    (datetime(2026, 11, 9, 6, 0, tzinfo=KST), Priority.URGENT, True),        # already past
    (datetime(2026, 11, 9, 17, 0, tzinfo=KST), Priority.TODAY, False),       # later today
    (datetime(2026, 11, 10, 7, 0, tzinfo=KST), Priority.URGENT, False),      # tomorrow, <24h
    (datetime(2026, 11, 13, 18, 0, tzinfo=KST), Priority.THIS_WEEK, False),  # Friday
    (datetime(2026, 11, 15, 23, 59, tzinfo=KST), Priority.THIS_WEEK, False), # Sunday
    (datetime(2026, 11, 20, 12, 0, tzinfo=KST), Priority.THIS_MONTH, False),
    (datetime(2026, 12, 3, 16, 0, tzinfo=KST), Priority.LATER, False),
])
def test_date_priority(due, expected, overdue):
    assert date_priority(due, NOW) == (expected, overdue)


def test_weekend_this_week_means_coming_week():
    sunday = datetime(2026, 11, 8, 15, 0, tzinfo=KST)
    assert date_priority(datetime(2026, 11, 13, 18, 0, tzinfo=KST), sunday)[0] == Priority.THIS_WEEK
    assert date_priority(datetime(2026, 11, 20, 18, 0, tzinfo=KST), sunday)[0] == Priority.THIS_MONTH


def test_dates_escalate_model_priority():
    email = Email(id="e", received_at=NOW)
    item = finalize(email, tr(Priority.THIS_MONTH, deadline="2026-11-10T17:00:00+09:00"), NOW)
    assert item.final_priority == Priority.THIS_WEEK   # model said this_month, deadline is this week
    # VIP-style over-escalation is ignored when a date exists...
    item = finalize(email, tr(Priority.URGENT, deadline="2026-11-13T18:00:00+09:00"), NOW)
    assert item.final_priority == Priority.THIS_WEEK
    # ...unless the sender flagged it as high importance
    flagged = Email(id="f", received_at=NOW, importance="high")
    assert finalize(flagged, tr(Priority.URGENT, deadline="2026-11-13T18:00:00+09:00"), NOW).final_priority == Priority.URGENT
    # FYI mail with a date but nothing to do stays FYI
    item = finalize(email, tr(Priority.FYI, action=False, deadline="2026-11-10"), NOW)
    assert item.final_priority == Priority.FYI


# --- graph -----------------------------------------------------------------------
def test_graph_end_to_end(settings):
    script = {
        "s1": tr(Priority.TODAY, deadline="2026-11-10T17:00:00+09:00", events=[ExtractedEvent(
            title="[마감] 연구비 정산 증빙", kind="deadline", start="2026-11-10T17:00:00+09:00",
            all_day=False, evidence="11월 10일(화) 17시까지")]),
        "s3": tr(Priority.THIS_WEEK, deadline="2026-11-13T18:00:00+09:00", confidence=0.5, events=[ExtractedEvent(
            title="중간보고서", kind="deadline", start="2026-11-13T18:00:00+09:00",
            all_day=False, evidence="11. 13.(금) 18:00까지")]),
    }
    llm = ScriptedLLM(script, fail={"s5"})
    deps = make_deps(settings, llm)
    app = build_graph(deps, checkpointer=InMemorySaver())
    out = app.invoke(initial_state(NOW), {"configurable": {"thread_id": "t1"}})

    assert "s4" not in llm.calls                      # "(광고)" skipped by rule, no LLM call
    pri = {d["email"]["id"]: d["final_priority"] for d in out["digest_items"]}
    assert pri["s1"] == "this_week"                    # deadline 33h away → date decides, not the model's "today"
    assert pri["s5"] == "today"                        # LLM failure surfaces as "확인 필요"
    assert pri["s4"] == "fyi"
    assert [c["message_id"] for c in out["calendar_created"]] == ["s1"]   # high confidence only
    assert [c["message_id"] for c in out["needs_review"]] == ["s3"]       # low confidence → review
    assert list((settings.output_dir / "calendar").glob("*.ics"))
    assert "메일 브리핑" in out["digest_text"] and out["errors"]

    # Second run: nothing is re-triaged, open items carry over, no duplicate events.
    llm.calls.clear()
    out2 = build_graph(deps, checkpointer=InMemorySaver()).invoke(initial_state(NOW), {"configurable": {"thread_id": "t2"}})
    assert llm.calls == ["s5"]                         # only the failed one is retried
    assert out2["calendar_created"] == []
    assert any(d["carried_over"] for d in out2["digest_items"])


def test_interactive_review_interrupt(settings):
    script = {"s3": tr(deadline="2026-11-13T18:00:00+09:00", confidence=0.5, events=[ExtractedEvent(
        title="중간보고서", kind="deadline", start="2026-11-13T18:00:00+09:00", all_day=False, evidence="x")])}
    app = build_graph(make_deps(settings, ScriptedLLM(script)), checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t3"}}
    out = app.invoke(initial_state(NOW, interactive=True), cfg)
    assert "__interrupt__" in out
    key = out["__interrupt__"][0].value["needs_review"][0]["key"]
    out = app.invoke(Command(resume=[key]), cfg)
    assert [c["message_id"] for c in out["calendar_created"]] == ["s3"]
    assert out["needs_review"] == []


def test_daily_checklist_event(settings):
    from mail_agent.calendars import build_checklist_spec

    settings.checklist_time = "08:00"
    script = {
        "s1": tr(Priority.TODAY, deadline="2026-11-09T17:00:00+09:00"),
        "s3": tr(Priority.THIS_WEEK, deadline="2026-11-13T18:00:00+09:00"),
    }
    deps = make_deps(settings, ScriptedLLM(script))
    out = build_graph(deps, checkpointer=InMemorySaver()).invoke(initial_state(NOW), {"configurable": {"thread_id": "c1"}})
    cl = out["checklist"]
    assert cl["title"].startswith("✅ 오늘 할 일 1건") and cl["lines"] >= 2
    assert "체크리스트" in out["digest_text"]
    # 07:30 run → alarm at 08:00; a run after 08:00 → alarm a few minutes later, never in the past
    items = [finalize(Email(id="x", received_at=NOW), script["s1"], NOW)]
    assert build_checklist_spec(items, NOW, settings, "me").start.hour == 8
    late = NOW.replace(hour=15, minute=41)
    spec = build_checklist_spec(items, late, settings, "me")
    assert spec.start > late and spec.start.minute % 5 == 0 and spec.reminder_minutes == 0
    # same day + same calendar → same key, so the event is updated instead of duplicated
    assert spec.key == build_checklist_spec(items, NOW, settings, "me").key


def test_outlook_duplicate_transaction_id_updates_existing(settings):
    from mail_agent.calendars import EventSpec, OutlookCalendar

    class Resp:
        text = '{"error":{"code":"ErrorDuplicateTransactionId"}}'

    class Err(Exception):
        response = Resp()

    class FakeGraph:
        def __init__(self):
            self.patched = []

        def post(self, path, json):
            raise Err()

        def get(self, path, params=None, headers=None):
            return {"value": [{"id": "other", "transactionId": "zzz"}, {"id": "E1", "transactionId": "k1"}]}

        def patch(self, path, json):
            self.patched.append(path)

    g = FakeGraph()
    cal = OutlookCalendar(g, settings)
    spec = EventSpec("k1", "m", "✅ 오늘 할 일", "checklist", NOW, NOW, False, None, "body", 0)
    assert cal.create(spec) == "E1" and g.patched == ["/me/events/E1"]
