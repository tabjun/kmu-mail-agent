/* Dashboard: accounts & status first, compact to-do (expand for details), cleanup approvals, settings. */
"use strict";

const listHtml = (arr) => (arr && arr.length ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "<span class='muted'>없음</span>");
const PRIO_ORDER = ["urgent", "today", "this_week", "this_month", "later"];
const PRIO_SHORT = { urgent: "긴급", today: "오늘", this_week: "이번 주", this_month: "이번 달", later: "이후" };
const WD = "일월화수목금토";
const offsetText = (m) => (m % 1440 === 0 ? `${m / 1440}일 전` : m % 60 === 0 ? `${m / 60}시간 전` : `${m}분 전`);

function whenText(iso) {
  if (!iso) return "기한 없음";
  const d = new Date(iso);
  const date = `${d.getMonth() + 1}/${d.getDate()}(${WD[d.getDay()]})`;
  const endOfDay = d.getHours() === 23 && d.getMinutes() === 59;
  return endOfDay ? date : `${date} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const ICON = { ok: "✅", warn: "⚠️", fail: "❌" };

async function refreshHealth() {
  const ul = $("health-list");
  $("recheck").disabled = true;
  try {
    const h = await api("/api/health/full");
    $("health-badge").textContent = { ok: "모두 정상", warn: "확인 필요", fail: "연결 문제" }[h.overall];
    $("health-badge").className = `pill ${h.overall}`;
    ul.innerHTML = h.checks.map((c) => `
      <li class="h-${c.status}"><span class="h-icon">${ICON[c.status]}</span>
        <div><strong>${esc(c.label)}</strong><div class="muted">${esc(c.detail)}</div>
          ${c.action && c.action.type === "reconnect" ? `<button class="small h-fix" data-reconnect="${esc(c.action.account)}">다시 연결</button>` : ""}
          ${c.links.length ? `<div class="h-links">${c.links.map((l) => l.url
            ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.when)} ${esc(l.title)} ↗</a>`
            : `<span>${esc(l.when)} ${esc(l.title)}</span>`).join("")}</div>` : ""}</div></li>`).join("");
    ul.querySelectorAll("[data-reconnect]").forEach((b) => b.addEventListener("click", async () => {
      const acct = b.dataset.reconnect;
      ul.querySelectorAll("[data-reconnect]").forEach((x) => (x.disabled = true));
      const note = document.createElement("div");
      note.className = "muted";
      b.after(note);
      try {
        await connectAccount(acct, (t) => { note.innerHTML = t; });
        note.textContent = "연결되었습니다. 다시 확인하는 중…";
        refreshAccounts(); refreshHealth();
      } catch (err) { note.textContent = `연결하지 못했습니다: ${err.message}`; b.disabled = false; }
    }));
  } catch (e) {
    ul.innerHTML = `<li class="h-fail"><span class="h-icon">❌</span><div><strong>서버 연결</strong><div class="muted">메일 비서 서버에 연결할 수 없습니다.</div></div></li>`;
  } finally { $("recheck").disabled = false; }
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const m = s.monitor;
    $("onboarding").hidden = s.setup_completed;
    const r = [m.last_run, s.manual_run.last].filter((x) => x && x.at).sort((a, b) => (a.at < b.at ? 1 : -1))[0];
    $("last-check").textContent = r ? `마지막 메일 확인: ${r.at.replace("T", " ").slice(0, 16)} · 새 메일 ${r.new ?? "-"}통${r.error ? " · 오류: " + r.error : ""}` : "";
    const last = s.manual_run.last;
    if (last) {
      $("run-details").hidden = false;
      $("run-result").textContent = last.ok ? last.digest : `실패: ${last.error}`;
    }
    $("run-now").disabled = s.manual_run.running;
    $("run-now").textContent = s.manual_run.running ? "확인하는 중…" : "지금 메일 확인";
  } catch (e) { /* health card shows server problems */ }
}

async function refreshTodo() {
  try {
    const items = await api("/api/items?days=30");
    const todo = items.filter((i) => PRIO_ORDER.includes(i.final_priority) && !i.done).slice(0, 20);
    $("todo-count").textContent = todo.length ? `${todo.length}건` : "";
    if (!todo.length) { $("todo-list").innerHTML = "<p class='muted'>지금 처리할 일이 없습니다.</p>"; return; }
    $("todo-list").innerHTML = `<ul class="todo">${todo.map((i) => `
      <li><details>
        <summary>
          <span class="prio p-${i.final_priority}">${PRIO_SHORT[i.final_priority]}</span>
          <span class="when">${esc(whenText(i.due))}</span>
          <span class="what">${esc(i.title)}</span>
          ${i.overdue ? '<span class="tag del">기한 지남</span>' : ""}
        </summary>
        <div class="todo-detail">
          <p>${esc(i.summary)}</p>
          ${i.action_items.length ? `<ul>${i.action_items.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : ""}
          <p class="muted">✉ ${esc(i.sender)} · ${esc(i.subject)} · <span class="tag">${esc(i.category_label)}</span></p>
          <button class="secondary small" data-done="${esc(i.id)}">완료 표시</button>
        </div>
      </details></li>`).join("")}</ul>`;
    document.querySelectorAll("[data-done]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      await api(`/api/items/${encodeURIComponent(b.dataset.done)}/done`, { method: "POST" });
      refreshTodo();
    }));
  } catch (e) { $("todo-list").innerHTML = `<p class="muted">불러오기 실패: ${esc(e.message)}</p>`; }
}

async function refreshActions() {
  const box = $("actions-list");
  try {
    const list = await api("/api/actions?status=pending");
    $("badge").hidden = !list.length;
    $("badge").textContent = list.length;
    box.replaceChildren();
    if (!list.length) {
      box.innerHTML = `<p class='muted'>승인을 기다리는 정리 제안이 없습니다. 불필요한 메일이 오면 여기서 '지워도 될까요?'를 묻습니다.</p>
        <button class="secondary" id="empty-rule">＋ 정리할 메일 규칙 추가</button>`;
      $("empty-rule").addEventListener("click", () => openChatWindow("cleanup"));
    }
    list.forEach((a) => actionCard(a, box, () => { refreshActions(); refreshStatus(); }));
  } catch (e) { box.innerHTML = `<p class="muted">불러오기 실패: ${esc(e.message)}</p>`; }
}

async function refreshAccounts() {
  const ul = $("account-list");
  try {
    const r = await api("/api/accounts");
    ul.replaceChildren();
    if (!r.configured) ul.innerHTML = "<li class='muted'>앱 등록 정보(MS_CLIENT_ID)가 없습니다.</li>";
    else if (!r.accounts.length) ul.innerHTML = "<li class='warn'>연결된 계정이 없습니다. '+ 계정 추가'를 눌러 연결하세요.</li>";
    (r.details || r.accounts.map((u) => ({ username: u, label: "" }))).forEach((d) => {
      const a = d.username;
      const li = document.createElement("li");
      li.innerHTML = `<span><span class="ok">●</span> ${esc(a)} <span class="muted">${esc(d.label || "")}</span></span>`;
      const rm = Object.assign(document.createElement("button"), { textContent: "연결 해제", className: "link" });
      rm.addEventListener("click", async () => {
        if (!confirm(`${a} 연결을 해제할까요?`)) return;
        await api("/api/accounts/remove", { method: "POST", body: JSON.stringify({ username: a, type: d.type }) });
        refreshAccounts();
      });
      li.append(rm);
      ul.append(li);
    });
  } catch (e) { ul.innerHTML = `<li class="muted">불러오기 실패: ${esc(e.message)}</li>`; }
}

async function refreshPrefs() {
  try {
    const p = await api("/api/preferences");
    const mode = { off: "정리 안 함", ask: "지우기 전에 물어보기", auto: "자동 정리" }[p.cleanup.mode];
    $("prefs-body").innerHTML = `<dl class="kv inline">
      <dt>오늘 할 일 알림</dt><dd>${p.calendar.checklist_time ? `매일 ${esc(p.calendar.checklist_time)}` : "끔"}</dd>
      <dt>회의 · 마감 알림</dt><dd>${(p.calendar.meeting_reminders.length ? p.calendar.meeting_reminders : [p.calendar.meeting_reminder_minutes]).map(offsetText).join(", ")} · ${(p.calendar.deadline_reminders.length ? p.calendar.deadline_reminders : [p.calendar.deadline_reminder_minutes]).map(offsetText).join(", ")}</dd>
      <dt>점검 알림</dt><dd>${p.calendar.precheck_days.length ? p.calendar.precheck_days.map((d) => `D-${d}`).join(", ") + ` ${esc(p.calendar.precheck_time)}` : "끔"}</dd>
      <dt>불필요한 메일</dt><dd>${esc(mode)} · 기준 ${p.cleanup.rules.length}개 · 예외 ${p.cleanup.keep_rules.length}개</dd>
      <dt>우선순위 규칙</dt><dd>${p.priority_rules.length}개 · 중요 발신자 ${p.vip_senders.length}명</dd>
      <dt>메일 확인</dt><dd>${p.monitor.enabled ? `${p.monitor.interval_minutes}분마다` : "끔"}</dd>
      <dt>분류 모델</dt><dd>${esc(p.triage_model.replace("claude-", ""))}</dd></dl>
      <details><summary>규칙 전체 보기</summary><dl class="kv">
        <dt>불필요한 메일 기준</dt><dd>${listHtml(p.cleanup.rules)}</dd>
        <dt>지우지 않는 예외</dt><dd>${listHtml(p.cleanup.keep_rules)}</dd>
        <dt>우선순위 규칙</dt><dd>${listHtml(p.priority_rules)}</dd>
        <dt>중요 발신자</dt><dd>${listHtml(p.vip_senders)}</dd></dl></details>`;
  } catch (e) { $("prefs-body").innerHTML = `<p class="muted">불러오기 실패: ${esc(e.message)}</p>`; }
}

async function loadModels() {
  const sel = $("model-select");
  try {
    const [m, p] = await Promise.all([api("/api/models"), api("/api/preferences")]);
    sel.replaceChildren(...Object.entries(m.choices).map(([id, label]) => new Option(label, id, false, id === p.triage_model)));
  } catch (e) { /* server down */ }
}
$("model-select").addEventListener("change", async (e) => {
  const v = e.target.value;
  await api("/api/preferences", { method: "PUT", body: JSON.stringify({ triage_model: v, chat_model: v }) });
  refreshPrefs(); refreshHealth();
});
loadModels();

function refreshAll() { refreshStatus(); refreshTodo(); refreshActions(); refreshAccounts(); refreshPrefs(); }
$("recheck").addEventListener("click", refreshHealth);
refreshHealth();
setInterval(refreshHealth, 5 * 60 * 1000);
window.refreshAll = refreshAll;

document.querySelectorAll("[data-agent]").forEach((b) => b.addEventListener("click", () => openChatWindow(b.dataset.agent)));
$("run-now").addEventListener("click", async () => { await api("/api/run", { method: "POST" }); refreshStatus(); });
window.addEventListener("focus", refreshAll);
refreshAll();
setInterval(refreshStatus, 15000);
setInterval(() => { refreshActions(); refreshTodo(); }, 60000);

bindTestAlarm();

// ---------- key file (.env) location banner ----------
async function refreshEnv(manual = false) {
  let st;
  try { st = await api("/api/env-status"); } catch (e) { return; }
  $("env-folder").textContent = st.folder;
  $("env-banner").hidden = st.has_key;
  const ok = $("env-ok");
  ok.hidden = !st.has_key;
  ok.innerHTML = st.has_key
    ? `🔑 키 파일(.env) 적용됨 · 위치 <code>${esc(st.folder)}</code>${st.fixed ? ` · ${esc(st.fixed)}` : ""}`
    : "";
  if (manual && !st.has_key) {
    $("env-msg").textContent = st.env_exists
      ? ".env 파일은 있지만 ROOT_API_KEY 값이 비어 있습니다. 받은 파일이 맞는지 확인해 주세요."
      : "아직 .env 파일이 보이지 않습니다. 위 폴더에 넣었는지 확인해 주세요.";
  }
  if (manual && st.has_key) refreshAll();
}
$("env-copy").addEventListener("click", async () => {
  const text = $("env-folder").textContent;
  try { await navigator.clipboard.writeText(text); $("env-copy").textContent = "복사됨 ✓"; }
  catch { const r = document.createRange(); r.selectNodeContents($("env-folder")); getSelection().removeAllRanges(); getSelection().addRange(r); }
  setTimeout(() => ($("env-copy").textContent = "주소 복사"), 2000);
});
$("env-recheck").addEventListener("click", () => refreshEnv(true));
refreshEnv();
