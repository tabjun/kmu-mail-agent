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

async function api(path, options = {}) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* not json */ }
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
        onChange && onChange();
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

function openChatWindow(agent) {
  const url = "/chat" + (agent ? `?agent=${encodeURIComponent(agent)}` : "");
  const w = window.open(url, "mailAgentChat", "width=560,height=820,menubar=no,toolbar=no,location=no");
  if (!w) location.href = url; // popup blocked → same tab
  else w.focus();
}
