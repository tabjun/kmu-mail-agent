/* Chat window bound to one scoped agent (?agent=cleanup|priority|calendar). Without ?agent=, shows a picker. */
"use strict";

const params = new URLSearchParams(location.search);
const agentId = params.get("agent");
const sessKey = `mailAgent.session.${agentId || "none"}`;
let sessionId = store(sessKey) || "";
let busy = false;

function addMessage(role, text, meta) {
  const el = document.createElement("div");
  el.className = `msg ${role === "user" ? "user" : "bot"}`;
  el.innerHTML = role === "user" ? esc(text).replace(/\n/g, "<br>") : md(text);
  if (meta) el.insertAdjacentHTML("beforeend", `<div class="meta">${esc(meta)}</div>`);
  $("messages").append(el);
  el.scrollIntoView({ block: "end" });
  return el;
}

function renderMailTable(card) {
  const el = document.createElement("div");
  el.className = "attachment";
  const cands = card.rows.filter((r) => r.cleanup_candidate).length;
  el.innerHTML = `<h4>읽은 메일 ${card.rows.length}통 · 정리 후보 ${cands}통${card.demo ? " · 데모(샘플 메일)" : ""}</h4>`;
  const t = document.createElement("table");
  t.className = "mails";
  t.innerHTML = "<thead><tr><th>받은 시각</th><th>내용</th><th>우선순위</th></tr></thead>";
  const tb = document.createElement("tbody");
  card.rows.forEach((r) => {
    const tr = document.createElement("tr");
    if (r.cleanup_candidate) tr.className = "cand";
    tr.innerHTML = `<td class="nowrap">${esc(r.received)}</td>
      <td><strong>${esc(r.subject)}</strong><br>${esc(r.summary)}<br>
        <span class="muted">${esc(r.sender)} · <span class="tag">${esc(r.category)}</span>${r.due ? ` · 마감 ${esc(r.due)}` : ""}</span>
        ${r.cleanup_candidate ? `<br><span class="tag del">정리 후보</span> <span class="muted">${esc(r.cleanup_reason)}</span>` : ""}</td>
      <td class="nowrap">${esc(r.priority)}</td>`;
    tb.append(tr);
  });
  t.append(tb);
  el.append(t);
  $("messages").append(el);
}

function renderAction(action) {
  const w = document.createElement("div");
  w.className = "attachment";
  actionCard(action, w, null, { hideRuleButton: true });
  $("messages").append(w);
}

async function send(text) {
  if (busy || !text.trim()) return;
  busy = true;
  $("send").disabled = true;
  addMessage("user", text);
  const thinking = addMessage("bot", "확인하는 중…");
  thinking.classList.add("thinking");
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ session_id: sessionId || null, agent: agentId, message: text, model: $("model").value }) });
    sessionId = r.session_id;
    store(sessKey, sessionId);
    thinking.remove();
    r.attachments.forEach((c) => {
      if (c.type === "mail_table") renderMailTable(c);
      if (c.type === "action") renderAction(c.action);
    });
    addMessage("bot", r.reply || "(응답 없음)", r.model.replace("claude-", ""));
    if (window.opener && !window.opener.closed) { try { window.opener.refreshAll && window.opener.refreshAll(); } catch (e) { /* ignore */ } }
  } catch (e) {
    thinking.remove();
    addMessage("bot", `오류가 났습니다: ${e.message}`);
  } finally {
    busy = false;
    $("send").disabled = false;
    $("messages").lastElementChild?.scrollIntoView({ block: "end" });
  }
}

async function showPicker() {
  $("picker").hidden = false;
  const agents = await api("/api/agents");
  $("agent-cards").innerHTML = agents.map((a) => `
    <a class="agent-card" href="/chat?agent=${esc(a.id)}">
      <span class="agent-emoji">${esc(a.emoji)}</span>
      <strong>${esc(a.name)}</strong>
      <span class="muted">${esc(a.description)}</span>
      <span class="ex">예) ${esc(a.examples[0] || "")}</span>
    </a>`).join("");
}

async function showChat() {
  $("chat").hidden = false;
  $("switch-agent").hidden = false;
  $("new-chat").hidden = false;
  const g = await api(`/api/agents/${encodeURIComponent(agentId)}/greeting`);
  document.title = `${g.agent.name} 도우미`;
  $("agent-emoji").textContent = g.agent.emoji;
  $("agent-name").textContent = `${g.agent.name} 도우미`;
  $("agent-desc").textContent = g.agent.description;
  $("chips").innerHTML = g.agent.examples.map((x) => `<button class="chip" type="button">${esc(x)}</button>`).join("");
  document.querySelectorAll("#chips .chip").forEach((c) => c.addEventListener("click", () => send(c.textContent)));

  let restored = false;
  if (sessionId) {
    try {
      const h = await api(`/api/chat/${encodeURIComponent(sessionId)}`);
      h.messages.forEach((m) => {
        if (m.type === "mail_table") renderMailTable(m);
        else if (m.type === "action") renderAction(m.action);
        else if (m.text) addMessage(m.role, m.text, m.model ? m.model.replace("claude-", "") : undefined);
      });
      restored = h.messages.length > 0;
    } catch (e) { /* new session */ }
  }
  if (!restored) addMessage("bot", g.text);  // opening built from stored data on the server (no model call)
}

async function init() {
  try {
    const m = await api("/api/models");
    Object.entries(m.choices).forEach(([id, label]) => $("model").append(new Option(label, id, false, id === m.default_chat)));
  } catch (e) { /* server down */ }
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); const v = $("input").value; $("input").value = ""; send(v); });
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
  $("new-chat").addEventListener("click", () => { store(sessKey, ""); location.reload(); });
  $("switch-agent").addEventListener("click", () => { location.href = "/chat"; });
  if (agentId) await showChat(); else await showPicker();
}

init();
