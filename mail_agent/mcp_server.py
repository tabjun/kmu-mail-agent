"""MCP server: lets Claude Desktop set up and operate the mail agent by conversation.

Claude Desktop config (print it with `python -m mail_agent mcp-config`):

    "kmu-mail-agent": {
      "command": "<project>/.venv/Scripts/python.exe",
      "args": ["-m", "mail_agent.mcp_server"],
      "env": {"PYTHONPATH": "<project>"}
    }

The tools are the same ones the web chatbot uses, and they write the same
data/preferences.json, so a setting made in Claude Desktop shows up in the web
console and vice versa. Claude Desktop asks the user before each tool call;
approve_cleanup additionally only moves mail to Deleted Items (recoverable).
"""
from __future__ import annotations

import json
import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .assistant import SYSTEM, Assistant
from .actions import execute_action
from .config import Settings
from .store import Store

logging.basicConfig(stream=sys.stderr, level=logging.INFO)  # stdout is the MCP channel

_settings = Settings()
_store = Store(_settings.db_path)
def _clone() -> Settings:
    import copy

    s = copy.copy(_settings)
    s.apply_preferences()
    return s


_assistant = Assistant(_clone, _store)


server = MCPServer(
    name="kmu-mail-agent",
    title="KMU Mail Agent",
    instructions=SYSTEM,
)


def _tool(name: str, args: dict) -> str:
    out, cards, _ = _assistant._call_tool(name, args, _clone())
    if name == "scan_mailbox" and cards:  # Desktop has no table widget: include all rows in the result
        data = json.loads(out)
        data["rows"] = cards[0]["rows"]
        return json.dumps(data, ensure_ascii=False)
    return out


@server.tool(description="연결된 Microsoft 계정, 현재 설정, 대기 중인 정리 제안, 마지막 실행 결과를 조회합니다.",
             annotations=ToolAnnotations(readOnlyHint=True))
def get_status() -> str:
    return _tool("get_status", {})


@server.tool(description="메일 비서 설정을 부분 업데이트합니다. patch 예: {\"cleanup\": {\"mode\": \"ask\"}}, "
                         "{\"calendar\": {\"precheck_days\": [3, 1], \"checklist_time\": \"08:00\"}}, "
                         "{\"priority_rules\": [...]}, {\"setup_completed\": true}. 리스트는 통째로 교체됩니다.")
def update_preferences(patch: dict) -> str:
    return _tool("update_preferences", {"patch": patch})


@server.tool(description="최근 메일을 읽어 메일마다 요약·분류·우선순위·정리 후보 여부를 돌려줍니다. "
                         "사용자에게 모든 메일을 간단한 표로 보여주세요.",
             annotations=ToolAnnotations(readOnlyHint=True))
def scan_mailbox(days: int = 7, limit: int = 60) -> str:
    return _tool("scan_mailbox", {"days": days, "limit": limit})


@server.tool(description="지울 메일 목록으로 정리 제안을 만듭니다(아직 아무것도 지우지 않음). "
                         "사용자가 동의하면 approve_cleanup으로 실행합니다.")
def propose_cleanup(message_ids: list[str], title: str | None = None) -> str:
    return _tool("propose_cleanup", {"message_ids": message_ids, "title": title})


@server.tool(description="대기 중인 정리 제안 목록을 봅니다.", annotations=ToolAnnotations(readOnlyHint=True))
def list_cleanup_proposals() -> str:
    return json.dumps(_store.list_actions("pending"), ensure_ascii=False)


@server.tool(description="사용자가 명시적으로 승인한 정리 제안을 실행합니다. 메일은 지운 편지함으로 옮겨집니다(복구 가능). "
                         "message_ids를 주면 그 메일만 옮깁니다.",
             annotations=ToolAnnotations(destructiveHint=True))
def approve_cleanup(action_id: int, message_ids: list[str] | None = None) -> str:
    from .service import graph_runtime

    s = _clone()
    rt = graph_runtime(s, _store)
    result = execute_action(_store.get_action(action_id), _store, rt.mail_actions,
                            only_ids=set(message_ids) if message_ids is not None else None)
    return json.dumps(result, ensure_ascii=False)


@server.tool(description="지금 메일 확인→분류→정리 제안→캘린더 등록→오늘 할 일 체크리스트 갱신을 한 번 실행합니다.")
def run_now() -> str:
    return _tool("run_now", {})


@server.prompt(description="메일 비서 처음 설정을 대화로 진행합니다.")
def setup_mail_agent() -> str:
    return ("KMU Mail Agent 초기 설정을 도와줘. get_status로 지금 상태를 확인하고, "
            "안내 순서대로 하나씩 물어보면서 설정을 저장해줘.")


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
