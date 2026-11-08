"""Calendar sinks: Outlook (Graph), Google Calendar, or .ics files (offline default).

Every event carries a stable `key` (hash of message id + event) so re-running
the job never creates duplicates: the Store remembers created keys, Outlook gets
it as `transactionId`, Google as the event `id`.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import CATEGORY_LABEL_KO, PRIORITY_LABEL_KO, Email, ExtractedEvent, Priority, TriagedEmail
from .prioritize import parse_when

log = logging.getLogger(__name__)
KIND_KO = {"deadline": "마감", "meeting": "회의", "event": "행사"}


@dataclass
class EventSpec:
    key: str
    message_id: str
    title: str
    kind: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None
    body: str
    reminder_minutes: int
    account: str | None = None        # mailbox the mail came from → calendar of that same account
    reminders: tuple[int, ...] = ()   # every alarm offset (Google keeps them all on one event)


def event_key(message_id: str, ev: ExtractedEvent) -> str:
    raw = f"{message_id}|{ev.kind}|{ev.start}|{ev.title}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def build_spec(email: Email, ev: ExtractedEvent, settings: Settings) -> EventSpec | None:
    tz = settings.timezone
    start = parse_when(ev.start, tz, end_of_day=False)
    if not start:
        return None
    all_day = ev.all_day or len(ev.start.strip()) == 10
    if all_day:
        start = start.replace(hour=0, minute=0)
        end = start + timedelta(days=1)
    elif ev.kind == "deadline":
        # A deadline becomes a 30-minute block that ends at the deadline.
        end = start
        start = end - timedelta(minutes=30)
    else:
        end = parse_when(ev.end, tz, end_of_day=False) or start + timedelta(hours=1)
    reminder = {"deadline": min(settings.deadline_reminders or [settings.deadline_reminder_minutes]),
                "meeting": min(settings.meeting_reminders or [settings.meeting_reminder_minutes]),
                "event": 60}.get(ev.kind, 60)
    title = ev.title if ev.title.startswith("[") else f"[{KIND_KO[ev.kind]}] {ev.title}"
    body = (
        f"메일: {email.subject}\n보낸 사람: {email.sender_name} <{email.sender_email}>\n"
        f"근거: \"{ev.evidence}\"\n" + (f"Outlook에서 열기: {email.web_link}\n" if email.web_link else "")
        + "\n(KMU Mail Agent가 메일에서 자동 추출한 일정입니다. 원문을 확인하세요.)"
    )
    offsets = {"deadline": settings.deadline_reminders, "meeting": settings.meeting_reminders}.get(ev.kind) or [reminder]
    return EventSpec(event_key(email.id, ev), email.id, title, ev.kind, start, end, all_day, ev.location, body, reminder,
                     email.account, tuple(sorted(set(offsets), reverse=True)))


def build_precheck_specs(email: Email, ev: ExtractedEvent, settings: Settings, now: datetime) -> list[EventSpec]:
    """'[점검 D-3] …' alarms before deadlines that need preparation (documents, reviews)."""
    if ev.kind != "deadline" or not ev.needs_prep or not settings.precheck_days:
        return []
    due = parse_when(ev.start, settings.timezone)
    if not due:
        return []
    hh, mm = (int(x) for x in settings.precheck_time.split(":"))
    specs = []
    for days in settings.precheck_days:
        start = (due - timedelta(days=days)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start <= now:
            continue
        base = ev.title[ev.title.find("]") + 1:].strip() if ev.title.startswith("[") else ev.title
        title = f"[점검 D-{days}] {base}"
        key = hashlib.sha256(f"{email.id}|precheck|{days}|{ev.start}|{ev.title}".encode()).hexdigest()[:32]
        body = (f"마감: {_fmt_short(due, settings.timezone)}\n지금 초안·자료를 점검하고 필요하면 피드백을 받으세요.\n\n"
                f"메일: {email.subject}\n보낸 사람: {email.sender_name} <{email.sender_email}>\n근거: \"{ev.evidence}\"")
        specs.append(EventSpec(key, email.id, title, "precheck", start, start + timedelta(minutes=15), False,
                               None, body, 0))
    return specs


def _offset_label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}일 전"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간 전"
    return f"{minutes}분 전"


def build_extra_reminder_specs(email: Email, ev: ExtractedEvent, settings: Settings, now: datetime) -> list[EventSpec]:
    """Outlook events hold a single alarm, so additional alarm times become short
    '[미리 알림 · 1시간 전] …' events that ring at their own start."""
    main = build_spec(email, ev, settings)
    if main is None or main.all_day or ev.kind not in ("deadline", "meeting"):
        return []
    offsets = settings.deadline_reminders if ev.kind == "deadline" else settings.meeting_reminders
    closest = min(offsets) if offsets else 0
    anchor = main.end if ev.kind == "deadline" else main.start  # deadline time / meeting start
    base = main.title[main.title.find("]") + 1:].strip() if main.title.startswith("[") else main.title
    specs = []
    for off in sorted(set(offsets), reverse=True):
        if off == closest:
            continue  # carried by the event itself
        start = anchor - timedelta(minutes=off)
        if start <= now:
            continue
        key = hashlib.sha256(f"{main.key}|remind|{off}".encode()).hexdigest()[:32]
        what = "마감" if ev.kind == "deadline" else "시작"
        body = f"{_fmt_short(anchor, settings.timezone)} {what}: {base}\n\n{main.body}"
        specs.append(EventSpec(key, email.id, f"[미리 알림 · {_offset_label(off)}] {base}", "reminder",
                               start, start + timedelta(minutes=5), False, None, body, 0, email.account))
    return specs


def event_specs(email: Email, ev: ExtractedEvent, settings: Settings, now: datetime) -> list[EventSpec]:
    """All calendar entries one extracted event produces: the event, pre-check alarms, extra alarms."""
    main = build_spec(email, ev, settings)
    if main is None:
        return []
    return [main] + build_precheck_specs(email, ev, settings, now) + build_extra_reminder_specs(email, ev, settings, now)


def build_test_alarm_spec(settings: Settings, now: datetime) -> EventSpec:
    start = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    key = hashlib.sha256(f"test-alarm|{start.isoformat()}".encode()).hexdigest()[:32]
    body = ("메일 비서 테스트 알림입니다. 이 알림이 PC와 휴대폰 Outlook 앱에 울리면 연동이 정상입니다.\n"
            "확인했으면 이 일정은 지워도 됩니다.")
    return EventSpec(key, "test-alarm", "🔔 메일 비서 테스트 알림", "reminder", start, start + timedelta(minutes=5),
                     False, None, body, 0)


class CalendarSink(Protocol):
    name: str

    def create(self, spec: EventSpec) -> str | None: ...

    def update(self, spec: EventSpec, event_id: str) -> str | None: ...


class IcsCalendar:
    """Writes one .ics per event under outputs/calendar/ (double-click to import)."""

    name = "ics"

    def __init__(self, settings: Settings):
        self.dir = settings.output_dir / "calendar"
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _fmt(dt: datetime, all_day: bool) -> str:
        if all_day:
            return f";VALUE=DATE:{dt.strftime('%Y%m%d')}"
        return f":{dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    def create(self, spec: EventSpec) -> str | None:
        esc = lambda s: (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//KMU Mail Agent//KO", "BEGIN:VEVENT",
            f"UID:{spec.key}@kmu-mail-agent",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART{self._fmt(spec.start, spec.all_day)}",
            f"DTEND{self._fmt(spec.end, spec.all_day)}",
            f"SUMMARY:{esc(spec.title)}",
            f"DESCRIPTION:{esc(spec.body)}",
        ]
        if spec.location:
            lines.append(f"LOCATION:{esc(spec.location)}")
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(spec.title)}",
                  f"TRIGGER:-PT{spec.reminder_minutes}M", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
        path = self.dir / f"{spec.start.strftime('%Y%m%d')}_{spec.key[:8]}.ics"
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return str(path)


    def update(self, spec: EventSpec, event_id: str) -> str | None:
        return self.create(spec)


class OutlookCalendar:
    name = "outlook"
    _WINDOWS_TZ = {"Asia/Seoul": "Korea Standard Time"}

    def __init__(self, client, settings: Settings):
        self.client = client
        self.username = getattr(client, "username", None)
        self.tzname = self._WINDOWS_TZ.get(settings.timezone.key, "UTC")
        self.tz = settings.timezone if self.tzname != "UTC" else timezone.utc

    def _dt(self, dt: datetime) -> dict:
        return {"dateTime": dt.astimezone(self.tz).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": self.tzname}

    def create(self, spec: EventSpec) -> str | None:
        payload = {
            "subject": spec.title,
            "body": {"contentType": "text", "content": spec.body},
            "start": self._dt(spec.start),
            "end": self._dt(spec.end),
            "isAllDay": spec.all_day,
            "isReminderOn": True,
            "reminderMinutesBeforeStart": spec.reminder_minutes,
            "categories": ["MailAgent"],
            "showAs": "free" if spec.kind in ("deadline", "checklist", "precheck", "reminder") else "busy",
            "transactionId": spec.key,  # Graph de-duplicates retries with the same id
        }
        if spec.location:
            payload["location"] = {"displayName": spec.location}
        try:
            return self.client.post("/me/events", payload).get("id")
        except Exception as e:
            # The event already exists in Outlook (e.g. local data was reset or the app reinstalled):
            # find it by its transactionId and refresh it instead of failing.
            if "ErrorDuplicateTransactionId" not in getattr(getattr(e, "response", None), "text", ""):
                raise
            existing = self.find_by_key(spec)
            if not existing:
                raise
            self.update(spec, existing)
            return existing

    def find_by_key(self, spec: EventSpec) -> str | None:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        start = (spec.start - timedelta(days=1)).astimezone(timezone.utc).strftime(fmt)
        end = (spec.end + timedelta(days=1)).astimezone(timezone.utc).strftime(fmt)
        data = self.client.get("/me/calendarView", params={
            "startDateTime": start, "endDateTime": end, "$select": "id,transactionId", "$top": "200"})
        return next((ev["id"] for ev in data.get("value", []) if ev.get("transactionId") == spec.key), None)


    def update(self, spec: EventSpec, event_id: str) -> str | None:
        """Refresh an event we created earlier (used for the daily checklist)."""
        self.client.patch(f"/me/events/{event_id}", {
            "subject": spec.title,
            "body": {"contentType": "text", "content": spec.body},
            "start": self._dt(spec.start),
            "end": self._dt(spec.end),
            "isReminderOn": True,
            "reminderMinutesBeforeStart": spec.reminder_minutes,
        })
        return event_id


# ---------------------------------------------------------------------------
# Daily checklist ("오늘 할 일") event: one per day per calendar, refreshed on
# every run, reminder at start so the phone alarms with the prioritized list.
# ---------------------------------------------------------------------------
CHECKLIST_BUCKETS = [Priority.URGENT, Priority.TODAY, Priority.THIS_WEEK, Priority.THIS_MONTH]
CHECKLIST_MAX_PER_BUCKET = {Priority.URGENT: 20, Priority.TODAY: 20, Priority.THIS_WEEK: 10, Priority.THIS_MONTH: 5}


def _fmt_short(dt: datetime, tz) -> str:
    d = dt.astimezone(tz)
    wd = "월화수목금토일"[d.weekday()]
    return f"{d.month}/{d.day}({wd})" + ("" if (d.hour, d.minute) == (23, 59) else f" {d:%H:%M}")


def build_checklist_spec(items: list[TriagedEmail], now: datetime, settings: Settings,
                         calendar_owner: str | None) -> EventSpec | None:
    if not settings.checklist_time:
        return None
    todo = [i for i in items if i.final_priority in CHECKLIST_BUCKETS]
    if not todo:
        return None
    hh, mm = (int(x) for x in settings.checklist_time.split(":"))
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start <= now:  # run after the configured time (e.g. a test at 3pm): alarm in a few minutes instead
        start = (now + timedelta(minutes=5)).replace(second=0, microsecond=0)
        start += timedelta(minutes=(5 - start.minute % 5) % 5)
    end = start + timedelta(minutes=30)

    counts = {p: sum(1 for i in todo if i.final_priority == p) for p in CHECKLIST_BUCKETS}
    n_today = counts[Priority.URGENT] + counts[Priority.TODAY]
    if n_today:
        title = f"✅ 오늘 할 일 {n_today}건" + (f" (긴급 {counts[Priority.URGENT]})" if counts[Priority.URGENT] else "")
    else:
        title = f"✅ 오늘 급한 일 없음 · 이번 주 {counts[Priority.THIS_WEEK]}건"

    multi_account = len({i.email.account for i in todo if i.email.account}) > 1
    lines: list[str] = []
    for p in CHECKLIST_BUCKETS:
        bucket = [i for i in todo if i.final_priority == p]
        if not bucket:
            continue
        lines.append(f"{PRIORITY_LABEL_KO[p]} ({len(bucket)})")
        for it in bucket[:CHECKLIST_MAX_PER_BUCKET[p]]:
            when = f"{_fmt_short(it.due, now.tzinfo)} · " if it.due else ""
            flag = " [기한 지남]" if it.overdue else ""
            lines.append(f"☐ {when}{it.triage.title or it.triage.summary}{flag}")
            if it.triage.action_items:
                lines.append(f"    ↳ {' / '.join(it.triage.action_items)}")
            src = it.email.sender_name or it.email.sender_email
            acct = f" · {it.email.account}" if multi_account and it.email.account else ""
            lines.append(f"    ✉ {src} · [{CATEGORY_LABEL_KO[it.triage.category]}]{acct}")
        if len(bucket) > CHECKLIST_MAX_PER_BUCKET[p]:
            lines.append(f"  … 외 {len(bucket) - CHECKLIST_MAX_PER_BUCKET[p]}건")
        lines.append("")
    lines.append(f"(KMU Mail Agent가 {now:%m/%d %H:%M}에 메일을 읽고 만든 체크리스트입니다. 끝낸 일은 Outlook 애드인이나 "
                 "`python -m mail_agent done <id>`로 완료 처리하세요.)")
    key = hashlib.sha256(f"checklist|{now:%Y-%m-%d}|{calendar_owner}".encode()).hexdigest()[:32]
    return EventSpec(key=key, message_id=f"checklist:{now:%Y-%m-%d}", title=title, kind="checklist",
                     start=start, end=end, all_day=False, location=None, body="\n".join(lines), reminder_minutes=0)


def route_key(message_id: str | None, account: str | None) -> tuple[str, str]:
    """('imap'|'graph', account). The same address can be both a Gmail (IMAP) mailbox and a
    Microsoft account, so the mailbox kind is part of the key."""
    return ("imap" if (message_id or "").startswith("imap:") else "graph", (account or "").lower())


class RoutingCalendar:
    """Puts each event in the calendar of the account its mail came from:
    Office 365 / Outlook mail → that account's Outlook calendar, Gmail → that Gmail's Google Calendar
    (when connected). Anything else — and the daily checklist — goes to the main calendar."""
    name = "routed"

    def __init__(self, default, by_account: dict | None = None):
        self.default = default
        self.by_account = by_account or {}
        self.username = getattr(default, "username", None)

    def pick(self, spec: EventSpec):
        return self.by_account.get(route_key(spec.message_id, spec.account)) or self.default

    def create(self, spec: EventSpec) -> str | None:
        sink = self.pick(spec)
        if sink is None:
            raise RuntimeError("일정을 넣을 캘린더가 없습니다. 계정을 연결하세요.")
        if spec.kind == "reminder" and sink.name == "google":
            return "merged-into-event"  # Google holds every alarm on the event itself
        return sink.create(spec)

    def update(self, spec: EventSpec, event_id: str) -> str | None:
        sink = self.pick(spec)
        if sink is None or (spec.kind == "reminder" and sink.name == "google"):
            return event_id
        return sink.update(spec, event_id)


def make_calendar(settings: Settings, graph_client=None) -> CalendarSink | None:
    p = settings.calendar_provider.lower()
    if p == "none":
        return None
    if p == "outlook":
        if graph_client is None:
            raise RuntimeError("CALENDAR_PROVIDER=outlook에는 Microsoft 로그인이 필요합니다.")
        return OutlookCalendar(graph_client, settings)
    if p == "google":  # legacy CLI switch: the first connected Google account
        from .google_cal import GoogleAuth, GoogleCalendar

        accts = GoogleAuth(settings).accounts()
        if not accts:
            raise RuntimeError("연결된 Google 캘린더가 없습니다.")
        return GoogleCalendar(settings, accts[0])
    return IcsCalendar(settings)
