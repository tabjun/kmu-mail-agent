"""CLI.

    python -m mail_agent check-key                # Claude API key works? (free call)
    python -m mail_agent auth                     # one-time Microsoft sign-in (device code)
    python -m mail_agent run                      # fetch Outlook → triage → calendar → digest
    python -m mail_agent run --source sample      # offline demo with samples/emails.json
    python -m mail_agent run --dry-run --interactive
    python -m mail_agent serve                    # triage API + Outlook add-in files (https://localhost:3000)
    python -m mail_agent done <message-id>        # mark an open item as done
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, Settings


def _setup_logging(verbose: bool, settings: Settings) -> None:
    (settings.data_dir / "logs").mkdir(exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # Started by pythonw.exe (auto-start service): no console, so send prints/uvicorn logs to a file.
        log = open(settings.data_dir / "logs" / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr),
                  logging.FileHandler(settings.data_dir / "logs" / "mail_agent.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "msal"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_check_key(settings: Settings) -> int:
    import anthropic

    if not settings.anthropic_api_key:
        print("ROOT_API_KEY / ANTHROPIC_API_KEY가 .env에 없습니다.")
        return 1
    client = anthropic.Anthropic(**settings.anthropic_client_kwargs())
    try:
        ids = [m.id for m in client.models.list(limit=50)]
    except anthropic.AuthenticationError:
        print("❌ 키가 유효하지 않습니다 (401).")
        return 1
    except anthropic.PermissionDeniedError as e:
        print(f"❌ 권한 없음 (403): {e}")
        return 1
    print("✅ API 키 유효. 사용 가능한 모델 일부:", ", ".join(ids[:8]))
    for m in {settings.triage_model, settings.digest_model}:
        print(f"   {'✅' if m in ids else '⚠ 목록에 없음'} {m}")
    return 0


def cmd_auth(args, settings: Settings) -> int:
    from .msgraph import GraphAuth, GraphClient

    auth = GraphAuth(settings)
    if args.remove:
        print("✅ 제거" if auth.remove_account(args.remove) else "해당 계정이 없습니다.")
    elif not args.list:
        username = auth.add_account()
        me = GraphClient(auth, username).me()
        print(f"✅ 로그인 완료: {me.get('displayName')} <{me.get('mail') or me.get('userPrincipalName')}>")
    accounts = auth.accounts()
    print("로그인된 계정:" if accounts else "로그인된 계정 없음")
    for a in accounts:
        print(f"  - {a}")
    return 0


def cmd_run(args, settings: Settings) -> int:
    from langgraph.types import Command

    from .llm import RuleBasedLLM
    from .service import run_pipeline
    from .sources import SampleMailSource
    from .store import Store

    settings.apply_preferences()  # chatbot-managed preferences (data/preferences.json)
    if args.dry_run:
        settings.dry_run = True
    if args.calendar:
        settings.calendar_provider = args.calendar
    if args.notify:
        settings.notifiers = [n.strip() for n in args.notify.split(",") if n.strip()]

    source = SampleMailSource(Path(args.sample_file)) if args.source == "sample" else None
    if source is not None:
        settings.cleanup_mode = "ask" if settings.cleanup_mode == "auto" else settings.cleanup_mode  # never auto-move sample ids
    now = datetime.fromisoformat(args.now).replace(tzinfo=settings.timezone) if args.now else None
    result = run_pipeline(settings, source=source, llm=RuleBasedLLM() if args.no_llm else None,
                          store=Store(Path(args.db)) if args.db else None, now=now, interactive=args.interactive)
    app, config = result.get("_app"), result.get("_config")
    while "__interrupt__" in result:  # human review of low-confidence calendar items
        payload = result["__interrupt__"][0].value
        approved = []
        print("\n❓ 날짜가 모호해서 자동 등록하지 않은 일정입니다. 등록할까요?")
        for r in payload["needs_review"]:
            ans = input(f"  - {r['title']} · {r['when']}\n    근거: \"{r['evidence'][:80]}\"  [y/N] ").strip().lower()
            if ans == "y":
                approved.append(r["key"])
        result = app.invoke(Command(resume=approved), config)
    for line in result.get("notified", []):
        logging.getLogger("mail_agent").info("notify → %s", line)
    return 0


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from .api import create_app

    cert_dir = Path.home() / ".office-addin-dev-certs"  # created by `npx office-addin-dev-certs install`
    cert, key = cert_dir / "localhost.crt", cert_dir / "localhost.key"
    ssl = {}
    if not args.http and cert.exists() and key.exists():
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    elif not args.http:
        print("⚠ 개발용 인증서가 없어 HTTP로 실행합니다. Outlook 애드인은 HTTPS가 필요하니 README의 인증서 단계를 먼저 하세요.")
    scheme = "https" if ssl else "http"
    print(f"설정 화면: {scheme}://localhost:{args.port}/   API: /api/health   애드인: /addin/taskpane.html")
    uvicorn.run(create_app(settings, start_monitor=not args.no_monitor), host=args.host, port=args.port,
                log_level="info", **ssl)
    return 0


def cmd_mcp_config(settings: Settings) -> int:
    import json
    import os

    py = str(PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    cfg = {"mcpServers": {"kmu-mail-agent": {
        "command": py, "args": ["-m", "mail_agent.mcp_server"], "env": {"PYTHONPATH": str(PROJECT_ROOT)}}}}
    appdata = os.environ.get("APPDATA", "~/Library/Application Support")
    print("Claude Desktop → 설정 → 개발자 → 구성 편집 (claude_desktop_config.json)에 아래를 합치세요:")
    print(f"  파일 위치: {appdata}/Claude/claude_desktop_config.json\n")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def cmd_done(message_id: str, settings: Settings) -> int:
    from .store import Store

    ok = Store(settings.db_path).mark_done(message_id)
    print("✅ 완료 처리" if ok else "해당 메일 id가 없습니다.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mail_agent", description="KMU 메일 → 우선순위 → 캘린더 → 아침 브리핑")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-key", help="Claude API 키 확인 (비용 없음)")
    au = sub.add_parser("auth", help="Microsoft 계정 추가 로그인 (학교/개인, device code)")
    au.add_argument("--list", action="store_true", help="로그인된 계정만 보기")
    au.add_argument("--remove", metavar="USERNAME", help="계정 로그아웃")
    r = sub.add_parser("run", help="메일 분류 + 브리핑 1회 실행")
    r.add_argument("--source", choices=["graph", "sample"], default="graph")
    r.add_argument("--sample-file", default=str(PROJECT_ROOT / "samples" / "emails.json"))
    r.add_argument("--calendar", choices=["ics", "outlook", "google", "none"])
    r.add_argument("--notify", help="쉼표 구분: console,file,outlook_mail,webhook")
    r.add_argument("--dry-run", action="store_true", help="캘린더에 실제로 쓰지 않음")
    r.add_argument("--interactive", action="store_true", help="모호한 일정은 물어보고 등록 (LangGraph interrupt)")
    r.add_argument("--no-llm", action="store_true", help="Claude 대신 키워드 규칙 (오프라인 테스트)")
    r.add_argument("--now", help="기준 시각 덮어쓰기 (테스트용), 예: 2026-11-09T07:30")
    r.add_argument("--db", help="SQLite 경로 덮어쓰기 (테스트용)")
    sv = sub.add_parser("serve", help="분류 API + Outlook 애드인 정적 파일 서버")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=3000)
    sv.add_argument("--http", action="store_true", help="인증서가 있어도 HTTP로 실행")
    sv.add_argument("--no-monitor", action="store_true", help="백그라운드 메일 확인을 끔")
    sub.add_parser("mcp-config", help="Claude Desktop용 MCP 설정 JSON 출력")
    d = sub.add_parser("done", help="열린 할 일을 완료 처리")
    d.add_argument("message_id")
    args = p.parse_args(argv)

    settings = Settings()
    _setup_logging(args.verbose, settings)
    if args.cmd == "check-key":
        return cmd_check_key(settings)
    if args.cmd == "auth":
        return cmd_auth(args, settings)
    if args.cmd == "run":
        return cmd_run(args, settings)
    if args.cmd == "serve":
        return cmd_serve(args, settings)
    if args.cmd == "mcp-config":
        return cmd_mcp_config(settings)
    if args.cmd == "done":
        return cmd_done(args.message_id, settings)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
