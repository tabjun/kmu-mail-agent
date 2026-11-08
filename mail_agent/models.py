"""Data models: the mail we read and the structured triage Claude returns."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Priority(str, Enum):
    URGENT = "urgent"          # 24h 이내 마감 / 즉시 대응 / 기한 지남
    TODAY = "today"            # 오늘 처리
    THIS_WEEK = "this_week"    # 이번 주(일요일)까지
    THIS_MONTH = "this_month"  # 이번 달 안
    LATER = "later"            # 그 이후
    FYI = "fyi"                # 행동 불필요 (공지, 광고, 자동 알림)


PRIORITY_ORDER = [p for p in Priority]  # most urgent first
PRIORITY_LABEL_KO = {
    Priority.URGENT: "🔴 긴급",
    Priority.TODAY: "🟠 오늘",
    Priority.THIS_WEEK: "🟡 이번 주",
    Priority.THIS_MONTH: "🟢 이번 달",
    Priority.LATER: "🔵 이후",
    Priority.FYI: "⚪ 참고",
}


class Category(str, Enum):
    ACADEMIC = "academic"        # 학사/교무 (수업, 성적, 강의평가, 학적)
    RESEARCH = "research"        # 연구/과제 (연구재단, IITP, 과제 보고서, 연구비)
    PUBLICATION = "publication"  # 논문 투고/심사/학회
    STUDENT = "student"          # 학생·대학원생 문의, 지도
    MEETING = "meeting"          # 회의/면담 일정
    ADMIN = "admin"              # 일반 행정 (예산, 구매, 출장, 인사, 증빙)
    EVENT = "event"              # 행사/세미나/특강 안내
    EXTERNAL = "external"        # 외부 기관/산학 협력
    NEWSLETTER = "newsletter"    # 뉴스레터/광고/자동 알림
    OTHER = "other"


CATEGORY_LABEL_KO = {
    Category.ACADEMIC: "학사", Category.RESEARCH: "연구과제", Category.PUBLICATION: "논문/학회",
    Category.STUDENT: "학생", Category.MEETING: "회의", Category.ADMIN: "행정",
    Category.EVENT: "행사", Category.EXTERNAL: "외부", Category.NEWSLETTER: "뉴스레터",
    Category.OTHER: "기타",
}


class Email(BaseModel):
    id: str
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    received_at: datetime
    body: str = ""
    importance: str = "normal"
    is_read: bool = False
    flagged: bool = False
    has_attachments: bool = False
    web_link: Optional[str] = None
    conversation_id: Optional[str] = None
    internet_message_id: Optional[str] = None  # RFC 822 Message-ID, same in Graph and Office.js
    account: Optional[str] = None  # which signed-in mailbox this came from


# ---------------------------------------------------------------------------
# Structured output schema sent to Claude (output_format=TriageResult).
# Keep it to JSON-schema features structured outputs supports: no numeric
# bounds, plain strings for datetimes (validated in prioritize.py).
# ---------------------------------------------------------------------------
class ExtractedEvent(BaseModel):
    title: str = Field(description="캘린더에 들어갈 짧은 한국어 제목. 예: '[마감] 연구재단 중간보고서 제출'")
    kind: Literal["deadline", "meeting", "event"] = Field(
        description="deadline=제출/회신 마감, meeting=참석해야 하는 회의·면담, event=세미나·행사"
    )
    start: str = Field(description="ISO 8601, +09:00 오프셋 포함. 마감은 마감 시각. 날짜만 알면 YYYY-MM-DD")
    end: Optional[str] = Field(default=None, description="ISO 8601 종료 시각, 모르면 null")
    all_day: bool = Field(description="시각 정보 없이 날짜만 있으면 true")
    location: Optional[str] = Field(default=None, description="장소 또는 온라인 링크, 없으면 null")
    evidence: str = Field(description="날짜/시간 근거가 된 메일 원문 구절을 그대로 인용")
    needs_prep: bool = Field(False, description="문서 작성·검토·자료 준비처럼 마감 전에 미리 손봐야 하는 일이면 true")


class TriageResult(BaseModel):
    title: str = Field("", description="할 일 목록에 보일 짧은 제목, 20자 안팎. 누가+무엇. 예: '김대학원 학생 면담 요청', '연구비 정산 증빙 제출'")
    summary: str = Field(description="메일 핵심을 한국어 1~2문장으로. 누가 무엇을 언제까지 요청하는지")
    category: Category
    priority: Priority
    action_required: bool = Field(description="수신자가 직접 해야 할 일(회신, 제출, 참석, 결재 등)이 있으면 true")
    action_items: list[str] = Field(description="수신자가 해야 할 구체적 행동 목록(한국어, 동사로 시작). 없으면 빈 배열")
    deadline: Optional[str] = Field(
        default=None, description="가장 중요한 마감 시각 ISO 8601(+09:00). 명시된 마감이 없으면 null"
    )
    events: list[ExtractedEvent] = Field(description="캘린더에 넣을 만한 마감/회의/행사. 없으면 빈 배열")
    confidence: float = Field(description="이 분류와 날짜 추출에 대한 확신도 0.0~1.0")
    reason: str = Field(description="우선순위를 그렇게 판단한 근거 한 문장")
    cleanup_candidate: bool = Field(False, description="사용자별 정리 기준에 맞는 불필요한 메일이면 true (지우기 후보)")
    cleanup_reason: Optional[str] = Field(None, description="정리 후보로 본 이유 한 문장, 아니면 null")


class TriagedEmail(BaseModel):
    """Email + Claude triage + deterministic post-processing."""

    email: Email
    triage: TriageResult
    final_priority: Priority
    due: Optional[datetime] = None
    overdue: bool = False
    source: Literal["llm", "rule", "fallback"] = "llm"
    carried_over: bool = False  # open item from a previous day
    user_corrected: bool = False  # priority fixed by the user in the add-in; never re-bucket
