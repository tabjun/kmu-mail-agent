"""User preferences, shaped through the setup chatbot (web or Claude Desktop via MCP).

Stored as data/preferences.json. Everything the professor decides in conversation
lands here: which accounts to watch, what counts as unnecessary mail and whether
to ask before deleting it, priority rules, calendar policy (pre-check alarms N days
before document deadlines, checklist time), monitoring interval and models.
The pipeline reads it on every run, so a change in the chat applies to the next run.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Team plan credit ($380/month) comfortably covers Sonnet for one mailbox, so the cheap tier is not offered.
MODEL_CHOICES = {
    "claude-sonnet-5-5": "Sonnet 5.5 (기본 · 균형)",
    "claude-opus-5-5": "Opus 5.5 (가장 정확 · 비용 2배)",
}
DEFAULT_MODEL = "claude-sonnet-5-5"


class CleanupPolicy(BaseModel):
    mode: Literal["off", "ask", "auto"] = Field(
        "ask", description="off=정리 안 함, ask=지우기 전에 승인받기, auto=규칙에 맞으면 바로 지운 편지함으로 이동")
    rules: list[str] = Field(default_factory=lambda: [
        "제목에 (광고)/[광고]가 있는 마케팅 메일",
        "참석 의무가 없는 학회·저널 홍보, Call for Papers, 투고 권유(예: 'Invitation to Submit')",
        "서비스 가입/뉴스레터 자동 발송 메일",
    ], description="불필요한 메일로 볼 기준 (자연어)")
    keep_rules: list[str] = Field(default_factory=lambda: [
        "학생, 교내 부서, 연구재단, 공동연구자가 보낸 메일은 지우지 않는다",
        "논문 심사 결정(Decision) 메일과 영수증은 지우지 않는다",
    ], description="절대 지우지 말아야 할 기준 (정리 기준보다 우선)")


class CalendarPolicy(BaseModel):
    checklist_time: Optional[str] = Field("08:00", description="매일 '오늘 할 일' 체크리스트 일정 시각 HH:MM, null이면 끔")
    precheck_days: list[int] = Field(default_factory=lambda: [3, 1],
                                     description="준비가 필요한 마감(문서 작성·검토·제출) 전에 넣을 점검 알림 일수")
    precheck_time: str = Field("09:00", description="점검 알림 시각")
    deadline_reminder_minutes: int = Field(24 * 60, description="마감 일정 알림 (분 전)")
    meeting_reminder_minutes: int = Field(30, description="회의 일정 알림 (분 전)")
    auto_add_confidence: float = Field(0.8, description="이 확신도 이상이면 묻지 않고 캘린더에 추가")
    add_events: bool = Field(True, description="메일 속 행사/세미나도 '확인 필요'로 제안할지")


class MonitorPolicy(BaseModel):
    enabled: bool = Field(True, description="서버가 켜져 있는 동안 메일함을 주기적으로 확인")
    interval_minutes: int = Field(15, description="확인 주기(분)")


class Preferences(BaseModel):
    owner_description: str = Field("대학 교수", description="메일함 주인 소개 (분류 기준에 반영)")
    accounts: list[str] = Field(default_factory=lambda: ["all"], description="읽을 계정 주소 목록 또는 ['all']")
    calendar_account: Optional[str] = Field(None, description="일정을 넣을 Outlook 캘린더 계정 (null=첫 계정)")
    priority_rules: list[str] = Field(default_factory=lambda: [
        "학생의 면담·서명·논문 검토 요청은 할 일로 본다",
        "행정 부서의 제출·회신 요청은 마감일 기준으로 판단한다",
    ], description="우선순위 판단 규칙 (자연어)")
    vip_senders: list[str] = Field(default_factory=list, description="중요 발신자 (주소, 도메인 또는 이름)")
    cleanup: CleanupPolicy = Field(default_factory=CleanupPolicy)
    calendar: CalendarPolicy = Field(default_factory=CalendarPolicy)
    monitor: MonitorPolicy = Field(default_factory=MonitorPolicy)
    triage_model: str = Field(DEFAULT_MODEL, description="메일 분류 모델")
    chat_model: str = Field("claude-sonnet-5-5", description="설정 챗봇 모델")
    setup_completed: bool = False

    def to_prompt_rules(self) -> str:
        lines = [f"- 메일함 주인: {self.owner_description}"]
        lines += [f"- 우선순위 규칙: {r}" for r in self.priority_rules]
        lines += [f"- 중요 발신자: {v} (요청을 놓치지 않도록 할 일과 마감을 꼼꼼히 추출)" for v in self.vip_senders]
        if self.cleanup.mode != "off":
            lines.append("- 정리(삭제) 후보 기준 (cleanup_candidate=true):")
            lines += [f"  · {r}" for r in self.cleanup.rules]
            lines.append("- 다음은 정리 후보에서 반드시 제외 (cleanup_candidate=false):")
            lines += [f"  · {r}" for r in self.cleanup.keep_rules]
        else:
            lines.append("- 메일 정리 기능이 꺼져 있으므로 cleanup_candidate는 항상 false")
        return "\n".join(lines)


class PreferencesStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> Preferences:
        with self._lock:
            if self.path.exists():
                p = Preferences.model_validate_json(self.path.read_text(encoding="utf-8"))
                # models no longer offered (e.g. an older Haiku setting) fall back to the default
                if p.triage_model not in MODEL_CHOICES:
                    p.triage_model = DEFAULT_MODEL
                if p.chat_model not in MODEL_CHOICES:
                    p.chat_model = DEFAULT_MODEL
                return p
            return Preferences()

    def save(self, prefs: Preferences) -> Preferences:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(prefs.model_dump_json(indent=2), encoding="utf-8")
        return prefs

    def update(self, patch: dict) -> Preferences:
        """Deep-merge a partial dict (from the chatbot) and validate."""
        current = json.loads(self.load().model_dump_json())

        def merge(dst: dict, src: dict) -> dict:
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    merge(dst[k], v)
                else:
                    dst[k] = v
            return dst

        merged = Preferences.model_validate(merge(current, patch))
        if merged.triage_model not in MODEL_CHOICES or merged.chat_model not in MODEL_CHOICES:
            raise ValueError(f"model must be one of {list(MODEL_CHOICES)}")
        return self.save(merged)
