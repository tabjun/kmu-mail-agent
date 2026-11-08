"""Mail sources other than Graph (sample JSON for offline runs and tests)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import Email


class MailSource(Protocol):
    name: str

    def fetch(self, since: datetime | None = None) -> list[Email]: ...


class SampleMailSource:
    name = "sample"

    def __init__(self, path: Path):
        self.path = path

    def fetch(self, since: datetime | None = None) -> list[Email]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        emails = [Email.model_validate(m) for m in data]
        return [e for e in emails if since is None or e.received_at >= since]
