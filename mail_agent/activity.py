"""Activity log: one JSON line per event in data/logs/activity.jsonl.

Records what happened end to end so problems can be diagnosed afterwards:
page views and clicks from the browser, every API call (method, path, status,
duration), account connections, settings changes, runs and agent turns.

Never logged: passwords, API keys, tokens, request bodies, or mail bodies.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()
_path: Path | None = None
MAX_BYTES = 5 * 1024 * 1024  # rotate at 5 MB


def init(log_dir: Path) -> None:
    global _path
    log_dir.mkdir(parents=True, exist_ok=True)
    _path = log_dir / "activity.jsonl"


def log_event(kind: str, **fields) -> None:
    if _path is None:
        return
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "kind": kind}
    for k, v in fields.items():
        if isinstance(v, str) and len(v) > 500:
            v = v[:500] + "…"
        rec[k] = v
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with _lock:
        try:
            if _path.exists() and _path.stat().st_size > MAX_BYTES:
                _path.replace(_path.with_suffix(".1.jsonl"))
            with _path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def tail(n: int = 200) -> list[dict]:
    if _path is None or not _path.exists():
        return []
    with _lock:
        lines = _path.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
