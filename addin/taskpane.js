/* KMU 메일 비서 - Outlook task pane.
 * Reads the open mail/appointment with Office.js, asks the triage API, renders
 * the result, and adds calendar candidates via displayNewAppointmentForm (the
 * user reviews and saves, so the add-in itself never writes to the calendar).
 */
"use strict";

const CFG = window.MAIL_AGENT_CONFIG || {};
const PRIORITY_COLOR = {
  urgent: "var(--urgent)", today: "var(--today)", this_week: "var(--this_week)",
  this_month: "var(--this_month)", later: "var(--later)", fyi: "var(--fyi)",
};
const WEEKDAY = "일월화수목금토";
const $ = (id) => document.getElementById(id);

let current = null;   // last TriageResponse
let request = null;   // last TriageRequest payload
let pca = null;       // MSAL nestable client (NAA), created lazily

// ---------- per-viewer settings (localStorage may be unavailable) ----------
function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key) || "";
    localStorage.setItem(key, value);
  } catch (e) { /* private mode / blocked storage: settings fall back to config.js */ }
  return value || "";
}
const apiBase = () => (store("mailAgent.apiBase") || CFG.apiBase || "").replace(/\/+$/, "");

// ---------- auth ----------
async function authHeaders() {
  if (CFG.clientId && CFG.apiScope && window.msal) {
    if (!pca) {
      pca = await msal.createNestablePublicClientApplication({
        auth: { clientId: CFG.clientId, authority: "https://login.microsoftonline.com/organizations" },
      });
    }
    const req = { scopes: [CFG.apiScope] };
    let res;
    try { res = await pca.acquireTokenSilent(req); }
    catch (e) { res = await pca.acquireTokenPopup(req); }
    return { Authorization: `Bearer ${res.accessToken}` };
  }
  const key = store("mailAgent.apiKey");
  return key ? { "X-API-Key": key } : {};
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, await authHeaders(), options.headers || {});
  const res = await fetch(apiBase() + path, Object.assign({}, options, { headers }));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* not json */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

// ---------- Office.js helpers ----------
function asyncCall(fn) {
  return new Promise((resolve, reject) =>
    fn((r) => (r.status === Office.AsyncResultStatus.Succeeded ? resolve(r.value) : reject(r.error))));
}

function restId(item) {
  try {
    if (!item.itemId) return null;
    return Office.context.mailbox.convertToRestId(item.itemId, Office.MailboxEnums.RestVersion.v2_0);
  } catch (e) {
    return item.itemId || null;
  }
}

async function readItem() {
  const item = Office.context.mailbox.item;
  if (!item) throw new Error("열린 메일이 없습니다.");
  const body = await asyncCall((cb) => item.body.getAsync(Office.CoercionType.Text, cb));
  const sender = item.from || item.organizer || {};
  const addrs = (list) => (list || []).map((r) => r.emailAddress).filter(Boolean).slice(0, 20);
  return {
    item_id: restId(item),
    internet_message_id: item.internetMessageId || null,
    subject: item.subject || "",
    sender_name: sender.displayName || "",
    sender_email: sender.emailAddress || "",
    to: addrs(item.to || item.requiredAttendees),
    cc: addrs(item.cc || item.optionalAttendees),
    received_at: (item.dateTimeCreated || new Date()).toISOString(),
    body: body || "",
    force: false,
  };
}

// ---------- rendering ----------
function fmtDate(iso, allDay) {
  if (!iso) return "";
  const d = new Date(iso);
  const date = `${d.getMonth() + 1}/${d.getDate()}(${WEEKDAY[d.getDay()]})`;
  if (allDay) return `${date} 종일`;
  const late = d.getHours() === 23 && d.getMinutes() === 59;
  return late ? date : `${date} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function setStatus(text, isError) {
  const el = $("status");
  el.textContent = text;
  el.className = "status" + (isError ? " error" : "");
  el.hidden = !text;
}

function render(r) {
  current = r;
  $("result").hidden = false;
  const badge = $("priority");
  badge.textContent = r.priority_label + (r.overdue ? " · 기한 지남" : "");
  badge.style.background = PRIORITY_COLOR[r.final_priority];
  $("category").textContent = r.category_label;
  $("cached").textContent = r.cached ? "아침 분석 결과" : "방금 분석";
  $("due").textContent = r.due ? `마감 ${fmtDate(r.due)}` : "";
  $("summary").textContent = r.summary;
  const ul = $("actions");
  ul.replaceChildren(...r.action_items.map((a) => Object.assign(document.createElement("li"), { textContent: a })));
  $("reason").textContent = `판단 근거: ${r.reason} (확신도 ${Math.round(r.confidence * 100)}%)`;
  $("btn-done").textContent = r.done ? "완료됨 ✓" : "완료";
  $("btn-done").disabled = r.done;
  $("fb-priority").value = r.final_priority;

  const cal = $("calendar");
  cal.replaceChildren();
  r.calendar.forEach((c) => {
    const card = document.createElement("div");
    card.className = "card";
    const h = Object.assign(document.createElement("h4"), { textContent: c.title });
    const when = Object.assign(document.createElement("div"), {
      className: "small", textContent: `${fmtDate(c.kind === "deadline" && !c.all_day ? c.end : c.start, c.all_day)}`
        + (c.location ? ` · ${c.location}` : ""),
    });
    const ev = Object.assign(document.createElement("div"), { className: "muted small", textContent: `근거: “${c.evidence}”` });
    const btn = Object.assign(document.createElement("button"), { textContent: "캘린더에 추가" });
    btn.addEventListener("click", () => openAppointment(c));
    card.append(h, when, ev, btn);
    cal.append(card);
  });
}

function openAppointment(c) {
  // Opens a pre-filled appointment; the user checks it and presses Save.
  Office.context.mailbox.displayNewAppointmentForm({
    requiredAttendees: [],
    optionalAttendees: [],
    start: new Date(c.start),
    end: new Date(c.end),
    location: c.location || "",
    subject: c.title,
    resources: [],
    body: c.body,
  });
}

// ---------- flows ----------
async function analyze(force) {
  $("result").hidden = true;
  setStatus(force ? "다시 분석하는 중…" : "분석하는 중…");
  try {
    request = await readItem();
    request.force = !!force;
    render(await api("/api/triage", { method: "POST", body: JSON.stringify(request) }));
    setStatus("");
  } catch (e) {
    setStatus(`분석 실패: ${e.message || e}. 설정(⚙)에서 API 주소를 확인하세요.`, true);
  }
}

async function markDone() {
  if (!current) return;
  try {
    await api(`/api/items/${encodeURIComponent(current.id)}/done`, { method: "POST" });
    current.done = true;
    render(current);
  } catch (e) { setStatus(`완료 처리 실패: ${e.message}`, true); }
}

async function sendFeedback() {
  if (!current) return;
  try {
    const r = await api(`/api/items/${encodeURIComponent(current.id)}/feedback`, {
      method: "POST",
      body: JSON.stringify({ priority: $("fb-priority").value, note: $("fb-note").value || null }),
    });
    render(r);
    $("fb-note").value = "";
    setStatus("고쳤습니다. 다음 분류부터 반영됩니다.");
  } catch (e) { setStatus(`수정 실패: ${e.message}`, true); }
}

async function loadList() {
  const status = $("list-status");
  const list = $("list");
  status.textContent = "불러오는 중…";
  list.replaceChildren();
  try {
    const items = await api("/api/items?days=14");
    status.textContent = items.length ? "" : "열린 할 일이 없습니다.";
    const groups = new Map();
    items.forEach((it) => {
      if (!groups.has(it.final_priority)) groups.set(it.final_priority, { label: it.priority_label, items: [] });
      groups.get(it.final_priority).items.push(it);
    });
    groups.forEach((g, key) => {
      const sec = Object.assign(document.createElement("div"), { className: "list-group" });
      const h = Object.assign(document.createElement("h3"), { textContent: `${g.label} (${g.items.length})` });
      h.style.borderLeftColor = PRIORITY_COLOR[key];
      sec.append(h);
      g.items.forEach((it) => {
        const row = Object.assign(document.createElement("div"), { className: "list-item" });
        row.append(
          Object.assign(document.createElement("div"), { textContent: it.summary }),
          Object.assign(document.createElement("div"), {
            className: "meta", textContent: [it.category_label, it.due ? `마감 ${fmtDate(it.due)}` : ""].filter(Boolean).join(" · "),
          }),
        );
        sec.append(row);
      });
      list.append(sec);
    });
  } catch (e) {
    status.textContent = `불러오기 실패: ${e.message}`;
  }
}

function showView(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  if (name === "list") loadList();
}

function initSettings() {
  $("set-api").value = apiBase();
  $("set-key").value = store("mailAgent.apiKey");
  $("auth-info").textContent = CFG.clientId
    ? "인증: Microsoft 계정 (NAA)"
    : "인증: 개발 모드 (localhost 또는 API 키)";
  $("btn-save").addEventListener("click", () => {
    store("mailAgent.apiBase", $("set-api").value.trim());
    store("mailAgent.apiKey", $("set-key").value.trim());
    showView("mail");
    analyze(false);
  });
}

Office.onReady((info) => {
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  $("btn-done").addEventListener("click", markDone);
  $("btn-retry").addEventListener("click", () => analyze(true));
  $("btn-feedback").addEventListener("click", sendFeedback);
  initSettings();
  if (info.host !== Office.HostType.Outlook) {
    setStatus("Outlook 안에서 열어 주세요.", true);
    return;
  }
  // Pinned task pane: re-analyze when the user selects another mail.
  try {
    Office.context.mailbox.addHandlerAsync(Office.EventType.ItemChanged, () => { showView("mail"); analyze(false); });
  } catch (e) { /* older clients without pinning support */ }
  analyze(false);
});
