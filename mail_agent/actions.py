"""Executes approved actions (human-in-the-loop guardrail).

Destructive steps never run straight from a model's tool call. The chatbot and the
background monitor only *propose* them (store.add_action → status "pending"); a
person approves in the web UI (or Claude Desktop's own tool-approval prompt for
MCP), and only then does this module touch the mailbox. Deletion = move to the
Deleted Items folder, so everything can be restored in Outlook.
"""
from __future__ import annotations

import logging

from .store import Store

log = logging.getLogger(__name__)


def execute_action(action: dict, store: Store, mail_actions, only_ids: set[str] | None = None) -> dict:
    if action is None:
        raise ValueError("unknown action")
    if action["status"] not in ("pending", "approved"):
        raise ValueError(f"action {action['id']} is already {action['status']}")
    if action["kind"] != "delete_mail":
        raise ValueError(f"unsupported action kind {action['kind']}")
    if mail_actions is None:
        raise RuntimeError("Microsoft 계정이 연결되어 있지 않아 메일을 옮길 수 없습니다.")
    moved, failed, skipped = [], [], []
    for item in action["items"]:
        if only_ids is not None and item["message_id"] not in only_ids:
            skipped.append(item["message_id"])
            continue
        try:
            mail_actions.move_to_deleted(item.get("account"), item["message_id"])
            store.mark_done(item["message_id"])  # not a to-do anymore
            moved.append(item["message_id"])
        except Exception as e:  # keep going; report per item
            log.warning("move failed for %s: %s", item["message_id"], e)
            failed.append({"message_id": item["message_id"], "error": str(e)[:200]})
    result = {"moved": len(moved), "failed": failed, "skipped": len(skipped)}
    store.set_action_status(action["id"], "done" if not failed else "failed", result)
    return result
