"""Triage API tests (offline: scripted LLM, temp SQLite)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from mail_agent.api import create_app
from mail_agent.models import ExtractedEvent, Priority
from mail_agent.store import Store

from .test_agent import ScriptedLLM, settings, tr  # noqa: F401  (fixture re-export)

MAIL = {
    "item_id": "AAMkAD-test-1",
    "internet_message_id": "<abc@kmu.ac.kr>",
    "subject": "[연구처] 정산 증빙 제출 (11/10 마감)",
    "sender_name": "연구지원팀",
    "sender_email": "research@kmu.ac.kr",
    "received_at": "2026-11-07T10:12:00+09:00",
    "body": "11월 10일(화) 17시까지 업로드해 주세요.",
}


def make_client(settings, llm):  # noqa: F811
    store = Store(settings.data_dir / "api.sqlite3")
    return TestClient(create_app(settings, store=store, llm=llm)), store


def test_triage_cache_feedback_done(settings):  # noqa: F811
    llm = ScriptedLLM({"AAMkAD-test-1": tr(Priority.TODAY, deadline="2099-11-10T17:00:00+09:00", events=[
        ExtractedEvent(title="[마감] 정산 증빙", kind="deadline", start="2099-11-10T17:00:00+09:00",
                       all_day=False, evidence="11월 10일(화) 17시까지")])})
    client, store = make_client(settings, llm)

    r = client.post("/api/triage", json=MAIL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False and body["calendar"][0]["title"] == "[마감] 정산 증빙"
    assert llm.calls == ["AAMkAD-test-1"]

    # Same mail by Message-ID only (e.g. moved folder → new Graph id): served from cache, no Claude call.
    r = client.post("/api/triage", json={**MAIL, "item_id": "different-id"})
    assert r.json()["cached"] is True and llm.calls == ["AAMkAD-test-1"]

    # User correction sticks and is fed into the next prompt.
    r = client.post("/api/items/AAMkAD-test-1/feedback", json={"priority": "fyi", "note": "참고용"})
    assert r.json()["final_priority"] == "fyi"
    assert client.post("/api/triage", json=MAIL).json()["final_priority"] == "fyi"
    assert store.recent_feedback()[0]["corrected_priority"] == "fyi"

    assert client.post("/api/items/AAMkAD-test-1/done").json() == {"ok": True}
    assert client.post("/api/items/nope/done").status_code == 404


def test_items_and_health(settings):  # noqa: F811
    client, _ = make_client(settings, ScriptedLLM({"AAMkAD-test-1": tr(Priority.THIS_WEEK)}))
    assert client.get("/api/health").json()["ok"] is True
    client.post("/api/triage", json={**MAIL, "received_at": "2099-01-01T09:00:00+09:00"})
    items = client.get("/api/items?days=36500").json()
    assert [i["id"] for i in items] == ["AAMkAD-test-1"]


def test_dev_auth_api_key(settings):  # noqa: F811
    settings.api_key = "secret"
    client, _ = make_client(settings, ScriptedLLM({}))
    assert client.post("/api/triage", json=MAIL).status_code == 401
    assert client.post("/api/triage", json=MAIL, headers={"X-API-Key": "secret"}).status_code == 200


def test_entra_mode_rejects_missing_token(settings):  # noqa: F811
    settings.api_auth_mode = "entra"
    client, _ = make_client(settings, ScriptedLLM({}))
    assert client.post("/api/triage", json=MAIL).status_code == 401
    assert client.post("/api/triage", json=MAIL, headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_addin_static_served(settings):  # noqa: F811
    client, _ = make_client(settings, ScriptedLLM({}))
    r = client.get("/addin/taskpane.html")
    assert r.status_code == 200 and "office.js" in r.text
    assert client.get("/addin/manifest.xml").status_code == 200


def test_imap_endpoint_accepts_json_body(settings, monkeypatch):  # noqa: F811
    import mail_agent.imap_mail as im

    seen = {}

    def fake_add(self, username, password, host=None, port=993):
        seen.update(username=username, password=password)
        return {"username": username, "host": "imap.gmail.com", "port": 993, "total": 3}

    monkeypatch.setattr(im.ImapAccountStore, "add", fake_add)
    client, _ = make_client(settings, ScriptedLLM({}))
    r = client.post("/api/accounts/imap", json={"username": "a@gmail.com", "password": "abcd efgh ijkl mnop"})
    assert r.status_code == 200, r.text
    assert seen == {"username": "a@gmail.com", "password": "abcdefghijklmnop"}  # spaces removed
