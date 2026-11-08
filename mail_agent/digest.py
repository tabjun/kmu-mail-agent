"""Render the morning digest (plain text for chat/console, HTML for mail)."""
from __future__ import annotations

import html
from datetime import datetime

from .llm import WEEKDAY_KO, DigestHeadline
from .models import CATEGORY_LABEL_KO, PRIORITY_LABEL_KO, PRIORITY_ORDER, Priority, TriagedEmail

MAX_FYI_LISTED = 8


def fmt_due(dt: datetime | None, now: datetime) -> str:
    if not dt:
        return ""
    d = dt.astimezone(now.tzinfo)
    time_part = "" if (d.hour, d.minute) == (23, 59) else f" {d:%H:%M}"
    return f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]}){time_part}"


def group(items: list[TriagedEmail]) -> dict[Priority, list[TriagedEmail]]:
    out: dict[Priority, list[TriagedEmail]] = {p: [] for p in PRIORITY_ORDER}
    for it in items:
        out[it.final_priority].append(it)
    return out


def items_for_llm(items: list[TriagedEmail], now: datetime, limit: int = 40) -> str:
    """Compact list sent to the digest model (no bodies, only triage output)."""
    lines = []
    for it in [i for i in items if i.final_priority != Priority.FYI][:limit]:
        t = it.triage
        lines.append(
            f"- [{it.final_priority.value}{'/기한지남' if it.overdue else ''}] {t.summary} "
            f"| 마감 {fmt_due(it.due, now) or '없음'} | 할 일: {'; '.join(t.action_items) or '-'} "
            f"| 보낸 사람: {it.email.sender_name or it.email.sender_email}"
        )
    return "\n".join(lines)


def _line(it: TriagedEmail, now: datetime) -> str:
    t = it.triage
    tags = [CATEGORY_LABEL_KO[t.category]]
    if it.overdue:
        tags.append("기한 지남")
    if it.carried_over:
        tags.append("이월")
    due = fmt_due(it.due, now)
    s = f"[{'/'.join(tags)}] {t.summary}"
    if due:
        s += f" — 마감 {due}"
    if t.action_items:
        s += f"\n    ↳ {' / '.join(t.action_items)}"
    s += f"\n    ✉ {it.email.sender_name or it.email.sender_email} · {it.email.subject}"
    return s


def render_text(items: list[TriagedEmail], now: datetime, headline: DigestHeadline | None,
                calendar_created: list[dict], needs_review: list[dict], usage: dict) -> str:
    g = group(items)
    new_count = sum(1 for i in items if not i.carried_over)
    out = [f"📬 {now.month}월 {now.day}일({WEEKDAY_KO[now.weekday()]}) 메일 브리핑 — 새 메일 {new_count}통"]
    if headline:
        out += ["", f"■ {headline.headline}"] + [f"  - {b.lstrip('-•* ')}" for b in headline.bullets]
    for p in PRIORITY_ORDER:
        bucket = g[p]
        if not bucket:
            continue
        out += ["", f"{PRIORITY_LABEL_KO[p]} ({len(bucket)})"]
        shown = bucket if p != Priority.FYI else bucket[:MAX_FYI_LISTED]
        out += [f"  {n}. {_line(it, now)}" for n, it in enumerate(shown, 1)]
        if len(bucket) > len(shown):
            out.append(f"  … 외 {len(bucket) - len(shown)}통")
    if calendar_created:
        out += ["", f"📅 캘린더에 추가 ({len(calendar_created)})"] + [f"  - {c['title']} · {c['when']}" for c in calendar_created]
    if needs_review:
        out += ["", f"❓ 확인 필요한 일정 ({len(needs_review)}) — 날짜가 모호해서 자동 등록하지 않음"]
        out += [f"  - {c['title']} · {c['when']} (근거: \"{c['evidence'][:60]}\")" for c in needs_review]
    out += ["", f"(Claude API 사용 추정 비용: ${usage.get('estimated_usd', 0):.3f})"]
    return "\n".join(out)


PRIORITY_COLOR = {
    Priority.URGENT: "#c62828", Priority.TODAY: "#ef6c00", Priority.THIS_WEEK: "#f9a825",
    Priority.THIS_MONTH: "#2e7d32", Priority.LATER: "#1565c0", Priority.FYI: "#757575",
}


def render_html(items: list[TriagedEmail], now: datetime, headline: DigestHeadline | None,
                calendar_created: list[dict], needs_review: list[dict], usage: dict) -> str:
    e = html.escape
    g = group(items)
    parts = [
        "<div style=\"font-family:'Malgun Gothic',Apple SD Gothic Neo,sans-serif;max-width:720px;color:#222\">",
        f"<h2 style='margin:0 0 8px'>📬 {now.month}월 {now.day}일({WEEKDAY_KO[now.weekday()]}) 메일 브리핑</h2>",
    ]
    if headline:
        parts.append(f"<div style='background:#f5f5f5;padding:12px 16px;border-radius:8px'><b>{e(headline.headline)}</b><ul>")
        parts += [f"<li>{e(b.lstrip('-•* '))}</li>" for b in headline.bullets]
        parts.append("</ul></div>")
    for p in PRIORITY_ORDER:
        bucket = g[p]
        if not bucket:
            continue
        color = PRIORITY_COLOR[p]
        parts.append(f"<h3 style='border-left:5px solid {color};padding-left:8px;margin-top:20px'>"
                     f"{PRIORITY_LABEL_KO[p]} <span style='color:#888'>({len(bucket)})</span></h3><ol>")
        for it in (bucket if p != Priority.FYI else bucket[:MAX_FYI_LISTED]):
            t = it.triage
            due = fmt_due(it.due, now)
            link = f" <a href='{e(it.email.web_link)}'>열기</a>" if it.email.web_link else ""
            tags = CATEGORY_LABEL_KO[t.category] + (" · 기한 지남" if it.overdue else "") + (" · 이월" if it.carried_over else "")
            parts.append(
                f"<li style='margin-bottom:8px'><span style='color:#888'>[{e(tags)}]</span> {e(t.summary)}"
                + (f" <b style='color:{color}'>마감 {e(due)}</b>" if due else "")
                + (f"<br><small>↳ {e(' / '.join(t.action_items))}</small>" if t.action_items else "")
                + f"<br><small style='color:#888'>✉ {e(it.email.sender_name or it.email.sender_email)} · {e(it.email.subject)}{link}</small></li>"
            )
        parts.append("</ol>")
    if calendar_created:
        parts.append(f"<h3>📅 캘린더에 추가 ({len(calendar_created)})</h3><ul>")
        parts += [f"<li>{e(c['title'])} · {e(c['when'])}</li>" for c in calendar_created]
        parts.append("</ul>")
    if needs_review:
        parts.append(f"<h3>❓ 확인 필요한 일정 ({len(needs_review)})</h3><ul>")
        parts += [f"<li>{e(c['title'])} · {e(c['when'])}<br><small>근거: “{e(c['evidence'][:80])}”</small></li>" for c in needs_review]
        parts.append("</ul>")
    parts.append(f"<p style='color:#aaa;font-size:12px'>KMU Mail Agent · Claude API 추정 비용 ${usage.get('estimated_usd', 0):.3f}</p></div>")
    return "\n".join(parts)
