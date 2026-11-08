"""Preferences, cleanup proposals (human approval), pre-check alarms, chatbot tools."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from mail_agent.api import create_app
from mail_agent.assistant import Assistant
from mail_agent.calendars import build_precheck_specs
from mail_agent.graph import build_graph, initial_state
from mail_agent.models import Category, Email, ExtractedEvent, Priority
from mail_agent.preferences import PreferencesStore
from mail_agent.store import Store

from .test_agent import NOW, ScriptedLLM, make_deps, settings, tr  # noqa: F401


class FakeMailActions:
    def __init__(self):
        self.moved = []

    def move_to_deleted(self, account, message_id):
        self.moved.append(message_id)
        return "x"


def ad(**kw):
    t = tr(Priority.FYI, action=False, cat=Category.NEWSLETTER)
    return t.model_copy(update={"cleanup_candidate": True, "cleanup_reason": "홍보 메일", **kw})


def test_preferences_merge_and_validation(settings):  # noqa: F811
    ps = PreferencesStore(settings.prefs_path)
    p = ps.update({"cleanup": {"mode": "auto"}, "calendar": {"precheck_days": [2]}})
    assert p.cleanup.mode == "auto" and p.cleanup.rules  # untouched nested fields survive
    assert p.calendar.precheck_days == [2] and p.calendar.checklist_time == "08:00"
    with pytest.raises(Exception):
        ps.update({"triage_model": "gpt-4"})
    settings.apply_preferences()
    assert settings.cleanup_mode == "auto" and settings.precheck_days == [2]
    assert "정리(삭제) 후보 기준" in p.to_prompt_rules()


def test_cleanup_ask_creates_pending_then_api_approves(settings):  # noqa: F811
    settings.cleanup_mode = "ask"
    deps = make_deps(settings, ScriptedLLM({"s7": ad(), "s5": ad()}))
    out = build_graph(deps, checkpointer=InMemorySaver()).invoke(initial_state(NOW), {"configurable": {"thread_id": "k"}})
    assert out["cleanup"]["mode"] == "ask" and out["cleanup"]["count"] == 3  # 2 by the model + "(광고)" by rule
    assert "정리 제안 3통" in out["digest_text"]
    pending = deps.store.list_actions("pending")
    assert len(pending) == 1 and {i["message_id"] for i in pending[0]["items"]} == {"s4", "s5", "s7"}

    # Second run must not propose the same mails again
    out2 = build_graph(deps, checkpointer=InMemorySaver()).invoke(initial_state(NOW), {"configurable": {"thread_id": "k2"}})
    assert out2["cleanup"] == {}

    # Approve only one of them through the API; the mailbox call goes through MailActions
    fake = FakeMailActions()
    import mail_agent.service as service

    real = service.graph_runtime
    service.graph_runtime = lambda st, store=None, need_graph=True: real(st, store, need_graph=False).__class__(
        st, store, None, [], None, fake)
    try:
        client = TestClient(create_app(settings, store=deps.store, llm=ScriptedLLM({})))
        r = client.post(f"/api/actions/{pending[0]['id']}/approve", json={"message_ids": ["s7"]})
        assert r.status_code == 200, r.text
        assert r.json()["moved"] == 1 and fake.moved == ["s7"]
        assert client.post(f"/api/actions/{pending[0]['id']}/approve", json={}).status_code == 400  # already done
    finally:
        service.graph_runtime = real


def test_cleanup_auto_moves_without_asking(settings):  # noqa: F811
    settings.cleanup_mode = "auto"
    deps = make_deps(settings, ScriptedLLM({"s7": ad()}))
    deps.mail_actions = FakeMailActions()
    out = build_graph(deps, checkpointer=InMemorySaver()).invoke(initial_state(NOW), {"configurable": {"thread_id": "a"}})
    assert out["cleanup"]["mode"] == "auto" and sorted(deps.mail_actions.moved) == ["s4", "s7"]
    assert deps.store.list_actions("pending") == []


def test_precheck_alarms_for_documents(settings):  # noqa: F811
    settings.precheck_days = [3, 1]
    email = Email(id="m", received_at=NOW, subject="보고서")
    ev = ExtractedEvent(title="[마감] 중간보고서 제출", kind="deadline", start="2026-11-13T18:00:00+09:00",
                        all_day=False, evidence="x", needs_prep=True)
    specs = build_precheck_specs(email, ev, settings, NOW)
    assert [s.title for s in specs] == ["[점검 D-3] 중간보고서 제출", "[점검 D-1] 중간보고서 제출"]
    assert specs[0].start.day == 10 and specs[0].start.hour == 9 and specs[0].reminder_minutes == 0
    assert build_precheck_specs(email, ev.model_copy(update={"needs_prep": False}), settings, NOW) == []
    # pre-checks already in the past are skipped
    assert len(build_precheck_specs(email, ev, settings, NOW.replace(day=11))) == 1


def test_assistant_tools_offline(settings):  # noqa: F811
    store = Store(settings.data_dir / "a.sqlite3")
    a = Assistant(lambda: settings, store)
    out, cards, changed = a._call_tool("update_preferences", {"patch": {"cleanup": {"mode": "off"}}}, settings)
    assert changed and json.loads(out)["preferences"]["cleanup"]["mode"] == "off"
    # propose_cleanup needs triaged mails in the store
    from mail_agent.prioritize import finalize

    store.save_triaged([finalize(Email(id="z", received_at=NOW, subject="(광고)"), ad(), NOW)])
    out, cards, _ = a._call_tool("propose_cleanup", {"message_ids": ["z"]}, settings)
    assert json.loads(out)["count"] == 1 and cards[0]["type"] == "action"
    assert "error" in json.loads(a._call_tool("propose_cleanup", {"message_ids": ["nope"]}, settings)[0])


def test_propose_cleanup_does_not_duplicate(settings):  # noqa: F811
    from mail_agent.prioritize import finalize

    store = Store(settings.data_dir / "d.sqlite3")
    a = Assistant(lambda: settings, store)
    store.save_triaged([finalize(Email(id="z", received_at=NOW, subject="(광고)"), ad(), NOW)])
    first = json.loads(a._call_tool("propose_cleanup", {"message_ids": ["z"]}, settings)[0])
    second = json.loads(a._call_tool("propose_cleanup", {"message_ids": ["z"]}, settings)[0])
    assert first["action_id"] == second["action_id"] and len(store.list_actions("pending")) == 1


def test_scoped_agents_guardrails(settings, monkeypatch):  # noqa: F811
    from mail_agent.agents import AGENTS, greeting
    from mail_agent.preferences import Preferences
    from mail_agent.prioritize import finalize

    store = Store(settings.data_dir / "g.sqlite3")
    a = Assistant(lambda: settings, store)
    cal, cleanup = AGENTS["calendar"], AGENTS["cleanup"]

    # 1) an agent cannot write settings outside its scope
    out = json.loads(a._call_tool("update_preferences", {"patch": {"cleanup": {"mode": "auto"}}}, settings, cal)[0])
    assert "error" in out and PreferencesStore(settings.prefs_path).load().cleanup.mode == "ask"
    ok = json.loads(a._call_tool("update_preferences", {"patch": {"calendar": {"checklist_time": "07:30"}}}, settings, cal)[0])
    assert ok["saved"] and PreferencesStore(settings.prefs_path).load().calendar.checklist_time == "07:30"

    # 2) tool whitelist: calendar agent has no mailbox tools
    assert "error" in json.loads(a._call_tool("propose_cleanup", {"message_ids": ["x"]}, settings, cal)[0])

    # 3) get_status only shows the agent's own settings
    st = json.loads(a._call_tool("get_status", {}, settings, cal)[0])
    assert set(st["preferences"]) == {"calendar"}

    # 4) find_mails_for_rule drops ids the model invents
    store.save_triaged([finalize(Email(id="ad1", received_at=NOW, subject="(광고) 할인"), ad(), NOW)])

    class FakeMatch:
        matching_ids = ["ad1", "invented-999"]

    monkeypatch.setattr("mail_agent.assistant.ClaudeLLM._parse", lambda self, **kw: FakeMatch())
    monkeypatch.setattr("mail_agent.assistant.ClaudeLLM.__init__", lambda self, s: None)
    found = json.loads(a._call_tool("find_mails_for_rule", {"rule": "광고", "days": 36500}, settings, cleanup)[0])
    assert [m["id"] for m in found["matches"]] == ["ad1"]

    # 5) the cleanup opening message is a template over stored data
    text = greeting("cleanup", Preferences(), store, NOW.replace(year=2026), owner="교수님")
    assert "어떤 내용의 메일을 정리할까요?" in text and "(광고) 할인" in text and "예시)" in text
