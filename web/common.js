/* Shared helpers for the dashboard (/) and the chat window (/chat). */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key) || "";
    localStorage.setItem(key, value);
  } catch (e) { /* storage may be blocked */ }
  return value || "";
}

function uiLog(type, extra) {
  try {
    const body = JSON.stringify(Object.assign({ type, page: location.pathname + location.search }, extra || {}));
    if (navigator.sendBeacon) navigator.sendBeacon("/api/log", new Blob([body], { type: "application/json" }));
    else fetch("/api/log", { method: "POST", headers: { "Content-Type": "application/json" }, body });
  } catch (e) { /* logging must never break the page */ }
}
window.addEventListener("error", (e) => uiLog("js_error", { message: String(e.message), source: e.filename, line: e.lineno }));
window.addEventListener("unhandledrejection", (e) => uiLog("js_error", { message: String(e.reason && e.reason.message || e.reason) }));
document.addEventListener("click", (e) => {
  const el = e.target.closest("button, a, summary, input[type=checkbox], input[type=radio]");
  if (!el) return;
  const label = (el.innerText || el.value || el.getAttribute("aria-label") || el.id || "").trim().slice(0, 60);
  uiLog("click", { target: `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}: ${label}` });
}, true);
window.addEventListener("load", () => uiLog("page_view"));

async function api(path, options = {}) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const d = (await res.json()).detail;
      detail = Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join(", ") : (d || detail);
    } catch (e) { /* not json */ }
    uiLog("api_fail", { target: `${(options.method || "GET")} ${path.split("?")[0]}`, detail: `${res.status} ${String(detail).slice(0, 200)}` });
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
    const m = line.match(/^\s*(?:[-•*]|\d+\.)\s+(.*)$/);
    if (m) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${m[1]}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += `<p>${line}</p>`;
    }
  }
  return html + (inList ? "</ul>" : "");
}

/* Approval card for a pending cleanup proposal. onChange is called after approve/reject. */
function actionCard(a, container, onChange, opts = {}) {
  const card = document.createElement("div");
  card.className = "card action-card";
  const done = a.status !== "pending";
  card.innerHTML = `<h3>🧹 ${esc(a.title || "정리 제안")}</h3>
    <p class="muted">승인하면 체크한 메일을 <strong>지운 편지함</strong>으로 옮깁니다. Outlook에서 언제든 복구할 수 있습니다.${done ? ` · 상태: ${esc(a.status)}` : ""}</p>`;
  const ul = document.createElement("ul");
  ul.className = "items";
  a.items.forEach((it) => {
    const li = document.createElement("li");
    li.innerHTML = `<label><input type="checkbox" ${done ? "disabled" : "checked"} value="${esc(it.message_id)}">
      <span><strong>${esc(it.subject)}</strong><small>${esc(it.sender)} · ${esc(it.reason || "")}</small></span></label>`;
    ul.append(li);
  });
  card.append(ul);
  if (!done) {
    const row = document.createElement("div");
    row.className = "row";
    const ok = Object.assign(document.createElement("button"), { textContent: "체크한 메일 정리" });
    const no = Object.assign(document.createElement("button"), { textContent: "그대로 두기", className: "secondary" });
    const note = document.createElement("p");
    note.className = "muted";
    ok.addEventListener("click", async () => {
      const ids = [...ul.querySelectorAll("input:checked")].map((i) => i.value);
      ok.disabled = no.disabled = true;
      try {
        const r = await api(`/api/actions/${a.id}/approve`, { method: "POST", body: JSON.stringify({ message_ids: ids }) });
        note.textContent = `✅ ${r.moved}통을 지운 편지함으로 옮겼습니다${r.failed.length ? ` (실패 ${r.failed.length}통)` : ""}.`;
        if (r.learning) watchLearning(a.id, note);
        else onChange && onChange();
      } catch (e) { note.textContent = `실패: ${e.message}`; ok.disabled = no.disabled = false; }
    });
    no.addEventListener("click", async () => {
      ok.disabled = no.disabled = true;
      await api(`/api/actions/${a.id}/reject`, { method: "POST" });
      note.textContent = "메일을 그대로 둡니다.";
      onChange && onChange();
    });
    row.append(ok, no);
    if (!opts.hideRuleButton) {
      const rule = Object.assign(document.createElement("button"), { textContent: "＋ 정리할 메일 규칙 추가", className: "secondary" });
      rule.addEventListener("click", () => openChatWindow("cleanup"));
      row.append(rule);
    }
    card.append(row, note);
  }
  container.append(card);
}

// After an approval the server learns rules from the checked / unchecked mails. Show what it learned.
async function watchLearning(actionId, note) {
  const base = note.textContent;
  note.textContent = `${base} 🧠 정리한 메일에서 규칙을 배우는 중…`;
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const st = await api(`/api/cleanup/learn-status/${actionId}`);
    if (st.status === "running") continue;
    if (st.status === "done" && st.rules.length) {
      note.innerHTML = `${esc(base)}<br>🧠 새 규칙 ${st.rules.length}개를 배웠습니다: ` +
        st.rules.map((r) => `<b>${r.kind === "keep" ? "남기기 · " : ""}${esc(r.text)}</b>`).join(" / ") +
        ` <button type="button" class="link" onclick="openLearnedRules()">배운 규칙 보기</button>`;
    } else if (st.status === "done") {
      note.textContent = `${base} 🧠 기존 규칙으로 충분해서 새로 배운 규칙은 없습니다.`;
    } else {
      note.textContent = `${base} (규칙 학습은 이번에 하지 못했습니다)`;
    }
    window.dispatchEvent(new Event("learned-rules-changed"));
    return;
  }
}

async function openLearnedRules() {
  let dlg = document.getElementById("learned-dialog");
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "learned-dialog";
    dlg.className = "learned-dialog";
    document.body.append(dlg);
  }
  const r = await api("/api/cleanup/learned");
  const rows = r.rules.slice().reverse().map((x) => `<li>
      <div class="lr-head"><span class="tag ${x.kind === "keep" ? "keep" : ""}">${x.kind === "keep" ? "남기기" : "정리"}</span>
        <b>${esc(x.text)}</b></div>
      <small class="muted">${esc((x.learned_at || "").replace("T", " "))} · 예: ${x.examples.map(esc).join(" / ") || "-"}</small>
      <button type="button" class="link danger" data-text="${esc(x.text)}">이 규칙 지우기</button></li>`).join("");
  dlg.innerHTML = `<div class="dlg-head"><h3>🧠 자동으로 배운 정리 규칙</h3><button type="button" class="link" data-close>닫기</button></div>
    <p class="muted">불필요한 메일 정리를 승인할 때, 체크한 메일(정리)과 체크를 푼 메일(남기기)을 보고 메일 비서가 스스로 만든 규칙입니다.
      다음 메일 확인부터 정리 후보를 고를 때 쓰입니다. 지우는 것은 여전히 승인한 뒤에만 합니다.</p>
    ${rows ? `<ul class="learned-list">${rows}</ul>` : `<p class="muted">아직 배운 규칙이 없습니다. 정리 제안을 승인하면 여기에 쌓입니다.</p>`}`;
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  dlg.querySelectorAll("[data-text]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`이 규칙을 지울까요?\n${b.dataset.text}`)) return;
    await api("/api/cleanup/learned/remove", { method: "POST", body: JSON.stringify({ text: b.dataset.text }) });
    window.dispatchEvent(new Event("learned-rules-changed"));
    openLearnedRules();
  }));
  if (!dlg.open) dlg.showModal();
}

function openChatWindow(agent) {
  const url = "/chat" + (agent ? `?agent=${encodeURIComponent(agent)}` : "");
  const w = window.open(url, "mailAgentChat", "width=560,height=820,menubar=no,toolbar=no,location=no");
  if (!w) location.href = url; // popup blocked → same tab
  else w.focus();
}

/* One-click Microsoft login. Opens the sign-in page in the browser; resolves when the app finished connecting.
   onUpdate(text) shows progress. Falls back to the device-code flow if the browser login cannot start. */
async function connectAccount(expected, onUpdate) {
  const say = (t) => onUpdate && onUpdate(t);
  say("Microsoft 로그인 창을 여는 중…");
  const start = await api("/api/accounts/login", { method: "POST", body: JSON.stringify({ expected }) });
  say("새로 열린 Microsoft 창에서 계정을 고르고 <strong>수락</strong>을 누르세요. 끝나면 여기서 자동으로 연결이 완료됩니다.");
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 2500));
    const st = await api(`/api/accounts/device-login/${encodeURIComponent(start.login_id)}`);
    if (st.status === "done") return st;
    if (st.status === "failed") throw new Error(st.error || "로그인하지 못했습니다");
  }
  throw new Error("시간이 초과되었습니다. 다시 시도해 주세요.");
}

// ---------- test alarm (setup step 2 + dashboard health card) ----------
function alarmHelpHtml(r) {
  const acct = esc(r.account);
  const nonMsDomain = r.personal && !/@(outlook|hotmail|live|msn)\./i.test(r.account);
  return `<details class="alarm-help"><summary>휴대폰에서 안 울렸나요? 이렇게 확인하세요</summary><ol>
    <li><b>일정이 들어갔는지</b> 먼저 확인: <a href="${esc(r.web_url)}" target="_blank" rel="noopener">Outlook 웹 캘린더</a>에서
      '🔔 메일 비서 테스트 알림'이 보이면 메일 비서 쪽은 정상입니다.</li>
    <li>휴대폰에 <b>Microsoft Outlook</b> 앱을 설치합니다. 아이폰은 App Store, 갤럭시는 Play 스토어에서 "Microsoft Outlook"을 검색하세요
      (파란 바탕에 흰 O 아이콘, 제작사 Microsoft Corporation).</li>
    <li>앱의 계정 목록에 <b>${acct}</b>${r.display_name ? ` (${esc(r.display_name)})` : ""}가 <b>Microsoft 계정</b>으로 있어야 합니다.
      같은 주소가 Google 'G' 아이콘으로 붙어 있다면 그건 Gmail 연결이라 이 캘린더 알림은 오지 않습니다.${nonMsDomain ? `
      <br><span class="warn">⚠ 이 주소는 Gmail 등 다른 회사 주소로 만든 <b>Microsoft 계정</b>입니다. 주소를 넣었을 때 Google 로그인 화면이 나오면
      Gmail 쪽으로 연결되는 것이니, 그 화면에서 계정 종류(공급자)를 바꾸는 메뉴를 눌러 <b>Outlook.com / Microsoft</b>를 고르세요.</span>` : ""}</li>
    <li>Outlook 앱 <b>설정 → 알림 → 캘린더</b>에서 알림을 켜고, 휴대폰 설정에서도 Outlook 앱 알림을 허용합니다.
      갤럭시라면 배터리 설정에서 Outlook을 '절전 제외'로 두면 늦게 울리는 일이 줄어듭니다.</li>
    <li>앱 캘린더에 테스트 일정이 보이면 <b>테스트 알림 보내기</b>를 한 번 더 눌러 확인합니다.</li>
  </ol></details>`;
}

function bindTestAlarm(btnId = "test-alarm", msgId = "test-alarm-msg") {
  const btn = document.getElementById(btnId);
  const msg = document.getElementById(msgId);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    msg.textContent = "Outlook 캘린더에 넣는 중…";
    try {
      const r = await api("/api/calendar/test-alarm", { method: "POST" });
      const who = r.display_name ? `${esc(r.display_name)} (${esc(r.account)})` : esc(r.account);
      msg.innerHTML = `✅ <b>${esc(r.when || r.at)}</b>에 울리는 '🔔 메일 비서 테스트 알림'을 넣었습니다.
        <br>계정 <b>${who}</b> · 캘린더 <b>${esc(r.calendar_name || "기본 캘린더")}</b>
        ${r.web_link ? ` · <a href="${esc(r.web_link)}" target="_blank" rel="noopener">Outlook에서 이 일정 열기 ↗</a>` : ""}
        <br>PC와 휴대폰 <b>Microsoft Outlook</b> 앱에서 울리는지 확인하세요.
        Outlook 화면이 '작업 주(월~금)'로 되어 있으면 주말 일정은 보이지 않으니 '일' 또는 '주' 보기로 확인하세요.${alarmHelpHtml(r)}`;
    } catch (e) { msg.textContent = e.message.replace(/^\d+ /, ""); }
    finally { btn.disabled = false; }
  });
}
