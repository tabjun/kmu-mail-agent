/* Setup wizard: 1) connect account  2) basic settings form  3) save & run. */
"use strict";

let accounts = [];
let accountDetails = [];

function go(step) {
  document.querySelectorAll(".step").forEach((s) => (s.hidden = s.id !== `step-${step}`));
  document.querySelectorAll(".progress li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("active", n === step);
    li.classList.toggle("done", n < step);
  });
  if (step === 2) fillForm();
  if (step === 3) renderSummary();
  history.replaceState(null, "", `/setup?step=${step}`);
  window.scrollTo(0, 0);
}

// ---------- step 1: accounts ----------
async function loadAccounts() {
  const r = await api("/api/accounts");
  accounts = r.accounts;
  accountDetails = r.details || accounts.map((u) => ({ username: u, type: "microsoft", label: "Microsoft" }));
  const ul = $("account-list");
  ul.replaceChildren();
  if (!accounts.length) ul.innerHTML = "<li class='muted'>아직 없습니다.</li>";
  accountDetails.forEach((a) => ul.insertAdjacentHTML("beforeend",
    `<li><span>✅ ${esc(a.username)}</span><span class="muted">${esc(a.label)}</span></li>`));
  $("to-2").disabled = !accounts.length;
}

async function connect(e) {
  e.preventDefault();
  const email = $("account-email").value.trim();
  const box = $("device");
  box.hidden = false;
  $("connect").disabled = true;
  try {
    const st = await connectAccount(email, (t) => { box.innerHTML = t; });
    box.innerHTML = st.mismatch
      ? `⚠ 입력한 주소(${esc(email)})와 다른 계정 <strong>${esc(st.username)}</strong>으로 연결됐습니다. 맞다면 그대로 진행하세요.`
      : `✅ ${esc(st.username)} 연결 완료`;
    $("account-email").value = "";
    $("connect").disabled = false;
    loadAccounts();
    return;
  } catch (err) {
    box.innerHTML = `브라우저 로그인을 완료하지 못했습니다 (${esc(err.message)}).<br>
      <button type="button" id="use-code" class="secondary small">코드 입력 방식으로 연결</button>`;
    $("connect").disabled = false;
    $("use-code").addEventListener("click", () => connectWithCode(email));
  }
}

async function connectWithCode(email) {
  const box = $("device");
  box.innerHTML = "로그인 코드를 만드는 중…";
  $("connect").disabled = true;
  try {
    const r = await api("/api/accounts/device-login", { method: "POST", body: JSON.stringify({ expected: email }) });
    box.innerHTML = `<ol>
      <li><a href="${esc(r.verification_uri)}" target="_blank" rel="noopener">${esc(r.verification_uri)}</a> 를 새 탭에서 엽니다.</li>
      <li>아래 코드를 입력합니다.<div class="code">${esc(r.user_code)}</div></li>
      <li><strong>${esc(email)}</strong> 계정을 선택하고, 권한 요청 화면에서 <strong>수락</strong>을 누릅니다.</li></ol>
      <p class="muted">완료되면 이 화면이 자동으로 바뀝니다… (15분 안에)</p>`;
    const poll = setInterval(async () => {
      const st = await api(`/api/accounts/device-login/${encodeURIComponent(r.user_code)}`);
      if (st.status === "waiting") return;
      clearInterval(poll);
      $("connect").disabled = false;
      if (st.status === "done") {
        box.innerHTML = st.mismatch
          ? `⚠ 입력한 주소(${esc(email)})와 다른 계정 <strong>${esc(st.username)}</strong>으로 연결됐습니다. 맞다면 그대로 진행하세요.`
          : `✅ ${esc(st.username)} 연결 완료`;
        $("account-email").value = "";
        loadAccounts();
      } else {
        box.innerHTML = `연결하지 못했습니다: ${esc(st.error || "알 수 없는 오류")}<br><small>학교에서 앱 사용 승인이 필요한 경우 관리자 승인 후 다시 시도하세요.</small>`;
      }
    }, 3000);
  } catch (err) {
    box.innerHTML = `실패: ${esc(err.message)}`;
    $("connect").disabled = false;
  }
}

// ---------- step 2: basic settings ----------
async function fillForm() {
  const p = await api("/api/preferences");
  $("f-checklist").value = p.calendar.checklist_time || "08:00";
  $("f-deadline").value = String(p.calendar.deadline_reminder_minutes);
  $("f-meeting").value = String(p.calendar.meeting_reminder_minutes);
  document.querySelectorAll(".f-pre").forEach((c) => (c.checked = p.calendar.precheck_days.includes(Number(c.value))));
  $("f-pretime").value = p.calendar.precheck_time;
  document.querySelectorAll("input[name=cleanup]").forEach((r) => (r.checked = r.value === p.cleanup.mode));
  $("f-interval").value = String(p.monitor.interval_minutes);
  $("f-model").value = p.triage_model;
  const cal = $("f-calendar");
  cal.replaceChildren();
  const msAccounts = accountDetails.filter((a) => a.type === "microsoft").map((a) => a.username);
  if (!msAccounts.length) cal.append(new Option("Microsoft 계정을 연결하면 Outlook 캘린더를 고를 수 있습니다", ""));
  msAccounts.forEach((a) => cal.append(new Option(`${a}의 Outlook 캘린더`, a, false, a === p.calendar_account)));
}

function formPatch() {
  return {
    accounts: ["all"],
    calendar_account: $("f-calendar").value || null,
    triage_model: $("f-model").value,
    chat_model: $("f-model").value,
    cleanup: { mode: document.querySelector("input[name=cleanup]:checked").value },
    calendar: {
      checklist_time: $("f-checklist").value || null,
      deadline_reminder_minutes: Number($("f-deadline").value),
      meeting_reminder_minutes: Number($("f-meeting").value),
      precheck_days: [...document.querySelectorAll(".f-pre:checked")].map((c) => Number(c.value)),
      precheck_time: $("f-pretime").value || "09:00",
    },
    monitor: { enabled: true, interval_minutes: Number($("f-interval").value) },
  };
}

async function saveStep2() {
  $("save-2").disabled = true;
  try {
    await api("/api/preferences", { method: "PUT", body: JSON.stringify(formPatch()) });
    go(3);
  } catch (e) { alert(`저장 실패: ${e.message}`); }
  finally { $("save-2").disabled = false; }
}

// ---------- step 3: summary & run ----------
async function renderSummary() {
  const p = await api("/api/preferences");
  const mode = { ask: "지우기 전에 물어보기", auto: "자동으로 정리", off: "정리하지 않기" }[p.cleanup.mode];
  $("summary").innerHTML = `<dl class="kv two-col">
    <dt>연결된 계정</dt><dd>${accounts.map(esc).join("<br>") || "없음"}</dd>
    <dt>일정을 넣을 캘린더</dt><dd>${esc(p.calendar_account || accounts[0] || "-")}</dd>
    <dt>오늘 할 일 알림</dt><dd>매일 ${esc(p.calendar.checklist_time || "끔")}</dd>
    <dt>점검 알림</dt><dd>${p.calendar.precheck_days.length ? p.calendar.precheck_days.map((d) => `D-${d}`).join(", ") + " " + esc(p.calendar.precheck_time) : "끔"}</dd>
    <dt>불필요한 메일</dt><dd>${esc(mode)}</dd>
    <dt>메일 확인</dt><dd>${p.monitor.interval_minutes}분마다</dd></dl>`;
}

async function run() {
  $("run").disabled = true;
  $("run-state").textContent = "메일을 읽고 분류하는 중… (메일 수에 따라 1~3분)";
  await api("/api/preferences", { method: "PUT", body: JSON.stringify({ setup_completed: true }) });
  await api("/api/run", { method: "POST" });
  const poll = setInterval(async () => {
    const s = await api("/api/status");
    if (s.manual_run.running) return;
    clearInterval(poll);
    $("run").disabled = false;
    const last = s.manual_run.last;
    if (last && last.ok) {
      $("run-state").innerHTML = `완료: 새 메일 ${last.new}통 분류, 캘린더 일정 ${last.calendar_created}건 추가${last.cleanup && last.cleanup.count ? `, 정리 제안 ${last.cleanup.count}통` : ""}`;
      $("run-result").hidden = false;
      $("run-result").textContent = last.digest;
      $("done-box").hidden = false;
    } else {
      $("run-state").textContent = `실행 실패: ${last ? last.error : "알 수 없음"}`;
    }
  }, 3000);
}

$("account-form").addEventListener("submit", connect);

// ---------- Gmail / Naver (IMAP, app password) ----------
document.querySelectorAll(".acct-tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".acct-tab").forEach((x) => x.classList.toggle("active", x === t));
  $("kind-ms").hidden = t.dataset.kind !== "ms";
  $("kind-imap").hidden = t.dataset.kind !== "imap";
}));
$("imap-email").addEventListener("change", async () => {
  const u = $("imap-email").value.trim();
  if (!u.includes("@")) return;
  try {
    const h = await api(`/api/accounts/imap-help?username=${encodeURIComponent(u)}`);
    $("imap-help").innerHTML = esc(h.help).replace(/(https:\/\/\S+)/, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  } catch (e) { /* ignore */ }
});
$("imap-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = $("device");
  box.hidden = false;
  box.textContent = "메일 서버에 연결하는 중…";
  $("imap-connect").disabled = true;
  try {
    const r = await api("/api/accounts/imap", { method: "POST",
      body: JSON.stringify({ username: $("imap-email").value.trim(), password: $("imap-pass").value }) });
    box.innerHTML = `✅ ${esc(r.username)} 연결 완료 · 받은편지함 ${r.total ?? "-"}통`;
    $("imap-pass").value = "";
    loadAccounts();
  } catch (err) {
    box.textContent = err.message.replace(/^400 /, "");
  } finally { $("imap-connect").disabled = false; }
});
$("to-2").addEventListener("click", () => go(2));
$("save-2").addEventListener("click", saveStep2);
$("run").addEventListener("click", run);
$("open-detail").addEventListener("click", () => openChatWindow());
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(Number(b.dataset.go))));

(async () => {
  await loadAccounts().catch(() => {});
  const step = Number(new URLSearchParams(location.search).get("step")) || 1;
  go(step === 1 || accounts.length ? step : 1);
})();
