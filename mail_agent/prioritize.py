"""Deterministic post-processing of Claude's triage.

Claude proposes a priority, but when a mail has a concrete deadline/meeting the
date decides the bucket (deterministic, explainable: "마감 11/13 → 이번 주").
Claude's own priority is used when there is no date, or when it is *more*
urgent and the sender marked the mail high-importance/flagged. A deadline
tomorrow can therefore never sit in "this month" because of a model slip, and a
VIP sender alone cannot push a Friday deadline into "긴급".
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import PRIORITY_ORDER, Email, Priority, TriagedEmail, TriageResult

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_when(value: str | None, tz: ZoneInfo, *, end_of_day: bool = True) -> datetime | None:
    """Parse ISO 8601 from Claude. Date-only values become 23:59 (deadline) or 00:00."""
    if not value:
        return None
    value = value.strip()
    try:
        if _DATE_ONLY.match(value):
            d = date.fromisoformat(value)
            return datetime.combine(d, time(23, 59) if end_of_day else time(0, 0), tzinfo=tz)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        return None


def date_priority(due: datetime, now: datetime) -> tuple[Priority, bool]:
    """Bucket a due datetime relative to now. Returns (priority, overdue)."""
    due_local = due.astimezone(now.tzinfo)
    if due_local < now:
        return Priority.URGENT, True
    if due_local - now <= timedelta(hours=24):
        # due within 24h: today if it is still today, otherwise urgent (e.g. tomorrow 9am)
        return (Priority.TODAY if due_local.date() == now.date() else Priority.URGENT), False
    end_of_week = now.date() + timedelta(days=6 - now.weekday())  # Sunday
    if now.weekday() >= 5:  # on Sat/Sun the morning brief's 'this week' means the coming week
        end_of_week += timedelta(days=7)
    if due_local.date() <= end_of_week:
        return Priority.THIS_WEEK, False
    if (due_local.year, due_local.month) == (now.year, now.month):
        return Priority.THIS_MONTH, False
    return Priority.LATER, False


def more_urgent(a: Priority, b: Priority) -> Priority:
    return a if PRIORITY_ORDER.index(a) <= PRIORITY_ORDER.index(b) else b


def finalize(email: Email, triage: TriageResult, now: datetime, source: str = "llm") -> TriagedEmail:
    tz = now.tzinfo
    candidates: list[datetime] = []
    deadline = parse_when(triage.deadline, tz)
    if deadline:
        candidates.append(deadline)
    for ev in triage.events:
        if ev.kind in ("deadline", "meeting"):
            start = parse_when(ev.start, tz, end_of_day=ev.kind == "deadline")
            if start:
                candidates.append(start)
    due = min(candidates) if candidates else None

    final, overdue = triage.priority, False
    # Dates only escalate when the user actually has to do something.
    if due and (triage.action_required or any(e.kind == "meeting" for e in triage.events)):
        by_date, overdue = date_priority(due, now)
        sender_marked_urgent = email.flagged or email.importance.lower() == "high"
        final = more_urgent(final, by_date) if sender_marked_urgent else by_date
    return TriagedEmail(email=email, triage=triage, final_priority=final, due=due, overdue=overdue, source=source)


def sort_key(item: TriagedEmail):
    far = datetime.max.replace(tzinfo=ZoneInfo("UTC"))
    return (PRIORITY_ORDER.index(item.final_priority), item.due or far, item.email.received_at)
