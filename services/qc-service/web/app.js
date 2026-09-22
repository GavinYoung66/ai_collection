(() => {
  const DEFAULT_API = "/api/v1";
  const LEGACY_API_BASES = new Set([
    "http://localhost:8003",
    "http://127.0.0.1:8003",
  ]);
  const STORE_API = "qc_api_base";
  const STORE_TOKEN = "qc_demo_token";
  const STORE_SESSIONS = "qc_demo_sessions";
  const params = new URLSearchParams(window.location.search);
  const stored = localStorage.getItem(STORE_API);

  const PENALTY = { high: 20, medium: 8, low: 2 };
  const SOURCE_LABEL = { ac: "AC", rule: "规则", llm: "LLM" };
  const META_LABEL = {
    session_id: "会话",
    call_id: "通话 ID",
    qc_record_id: "质检记录",
    compliance_score: "合规分",
    machine_score: "机检分",
    review_score: "人审分",
    grade: "等级",
    risk: "风险",
    qc_status: "质检状态",
    qc_result: "质检结论",
  };
  const STATUS_LABEL = {
    DONE: "已完成",
    PENDING_REVIEW: "待复核",
    REVIEWED: "已人审",
  };
  const RESULT_LABEL = {
    compliant: "合规",
    violation: "违规",
    pending_review: "待复核",
  };

  const els = {
    apiBase: document.getElementById("apiBase"),
    apiLabel: document.getElementById("apiLabel"),
    jwtToken: document.getElementById("jwtToken"),
    healthBar: document.getElementById("healthBar"),
    btnRefresh: document.getElementById("btnRefresh"),
    btnSeed: document.getElementById("btnSeed"),
    listFilter: document.getElementById("listFilter"),
    listBody: document.getElementById("listBody"),
    listPanel: document.getElementById("listPanel"),
    detailPanel: document.getElementById("detailPanel"),
    detailTitle: document.getElementById("detailTitle"),
    detailMeta: document.getElementById("detailMeta"),
    hitsBody: document.getElementById("hitsBody"),
    skippedList: document.getElementById("skippedList"),
    checkBanner: document.getElementById("checkBanner"),
    btnBack: document.getElementById("btnBack"),
    reviewBox: document.getElementById("reviewBox"),
    reviewHint: document.getElementById("reviewHint"),
    reviewDone: document.getElementById("reviewDone"),
    penaltyTotal: document.getElementById("penaltyTotal"),
    machineScoreLabel: document.getElementById("machineScoreLabel"),
    revisedScore: document.getElementById("revisedScore"),
    btnConfirm: document.getElementById("btnConfirm"),
    btnRescoreMode: document.getElementById("btnRescoreMode"),
    btnCloseQc: document.getElementById("btnCloseQc"),
    toast: document.getElementById("toast"),
    traceTimeline: document.getElementById("traceTimeline"),
    traceHint: document.getElementById("traceHint"),
    btnLangSmith: document.getElementById("btnLangSmith"),
    mcpNote: document.getElementById("mcpNote"),
  };

  /** @type {Map<string, object>} */
  let sessions = new Map();
  /** @type {Set<string>} */
  const pendingIds = new Set();
  /** @type {Set<string>} */
  const checkingIds = new Set();
  let currentSessionId = null;
  let currentRecord = null;
  let rescoreMode = false;

  function normalizeBase(raw) {
    return String(raw || "").replace(/\/$/, "");
  }

  function isLegacyDefault(raw) {
    return LEGACY_API_BASES.has(normalizeBase(raw));
  }

  function apiBase() {
    const value = normalizeBase(els.apiBase.value || DEFAULT_API);
    return value || DEFAULT_API;
  }

  function apiPrefix() {
    const base = apiBase();
    return /\/api\/v1$/i.test(base) ? base : `${base}/api/v1`;
  }

  function serviceRoot() {
    const base = apiBase();
    if (/\/api\/v1$/i.test(base)) {
      return base.slice(0, -"/api/v1".length);
    }
    return base;
  }

  function healthUrl() {
    const root = serviceRoot();
    return root ? `${root}/health` : "/health";
  }

  function token() {
    return (els.jwtToken.value || "").trim();
  }

  function syncLabel() {
    els.apiLabel.textContent = apiPrefix();
    localStorage.setItem(STORE_API, apiBase());
  }

  function persistToken() {
    localStorage.setItem(STORE_TOKEN, token());
  }

  function loadCachedSessions() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_SESSIONS) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (_) {
      return [];
    }
  }

  function saveCachedSessions() {
    localStorage.setItem(STORE_SESSIONS, JSON.stringify([...sessions.values()]));
  }

  function upsertSession(row) {
    if (!row || !row.session_id) return;
    const prev = sessions.get(row.session_id) || {};
    sessions.set(row.session_id, { ...prev, ...row });
    saveCachedSessions();
  }

  function showToast(msg, isErr) {
    els.toast.hidden = false;
    els.toast.textContent = msg;
    els.toast.classList.toggle("err", !!isErr);
  }

  function envelopeOk(code) {
    return code === "0";
  }

  async function api(path, options = {}) {
    const url = `${apiPrefix()}${path}`;
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const res = await fetch(url, { ...options, headers });
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      body = null;
    }
    const code = body && body.code;
    if (envelopeOk(code)) {
      return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
    }
    if (code === "A2011" && body && body.data) {
      return body.data;
    }
    const err = new Error((body && (body.message || body.detail)) || res.statusText || `API code ${code}`);
    err.code = code || String(res.status);
    err.data = body && body.data;
    throw err;
  }

  async function loadHealth() {
    if (!els.healthBar) return;
    try {
      const res = await fetch(healthUrl());
      let body = null;
      try {
        body = await res.json();
      } catch (_) {
        body = null;
      }
      const code = body && body.code;
      const data = (body && body.data) || {};
      if (!envelopeOk(code)) {
        els.healthBar.className = "health-bar bad";
        els.healthBar.textContent = `健康检查 degraded（code=${code || res.status}）`;
        return;
      }
      const ac = data.ac_version == null || data.ac_version === "" ? "—" : data.ac_version;
      const llm = data.llm_degraded ? "true" : "false";
      const rag = data.rag_connected ? "true" : "false";
      const bad = Boolean(data.llm_degraded) || data.rag_connected === false || !data.ac_version;
      els.healthBar.className = bad ? "health-bar bad" : "health-bar ok";
      els.healthBar.textContent = `ac_version=${ac} · llm_degraded=${llm} · rag_connected=${rag}`;
    } catch (_) {
      els.healthBar.className = "health-bar bad";
      els.healthBar.textContent = "健康检查不可达（degraded）";
    }
  }

  function scenarioTag(row) {
    const code = String(row.scenario || "").toUpperCase();
    const label = row.label || "";
    if (code === "A") return { cls: "", text: label || "A 合规核身" };
    if (code === "B") return { cls: "b", text: label || "B 中危" };
    if (code === "C") return { cls: "c", text: label || "C 红线" };
    return { cls: "", text: label || code || "—" };
  }

  function bucketOf(sessionId, row) {
    if (checkingIds.has(sessionId) || pendingIds.has(sessionId)) return "pending";
    if (row && row.qc_status === "PENDING_REVIEW") return "review";
    if (row && row.qc_status === "REVIEWED") return "reviewed";
    return "done";
  }

  function listStatus(sessionId, row) {
    if (checkingIds.has(sessionId)) return "开检中…";
    if (pendingIds.has(sessionId)) return "待检";
    if (row && row.qc_status) return STATUS_LABEL[row.qc_status] || row.qc_status;
    return "已出分";
  }

  function renderList() {
    const filter = (els.listFilter && els.listFilter.value) || "all";
    const rows = [...sessions.values()].reverse().filter((row) => {
      if (filter === "all") return true;
      return bucketOf(row.session_id, row) === filter;
    });
    if (!rows.length) {
      const empty =
        filter === "all"
          ? "暂无会话。点击「模拟三条待质检通话」。"
          : "当前筛选下没有通话。";
      els.listBody.innerHTML = `<tr><td colspan="5" class="muted">${empty}</td></tr>`;
      return;
    }
    els.listBody.innerHTML = rows
      .map((row) => {
        const id = row.session_id;
        const tag = scenarioTag(row);
        const checking = checkingIds.has(id);
        const callId = row.call_id && String(row.call_id) !== String(id) ? row.call_id : "—";
        const score = row.compliance_score != null ? String(row.compliance_score) : "—";
        return `<tr data-id="${escapeAttr(id)}" class="${checking ? "judging" : ""}">
          <td><span class="tag ${tag.cls}">${escapeHtml(tag.text)}</span></td>
          <td class="trunc"><code>${escapeHtml(id)}</code></td>
          <td><code>${escapeHtml(String(callId))}</code></td>
          <td class="status-cell">${escapeHtml(listStatus(id, row))}</td>
          <td>${escapeHtml(score)}</td>
        </tr>`;
      })
      .join("");
    els.listBody.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        const id = tr.getAttribute("data-id");
        onListRowClick(id);
      });
    });
  }

  async function loadList() {
    syncLabel();
    loadHealth();
    els.listBody.innerHTML = `<tr><td colspan="5" class="muted">加载中…</td></tr>`;
    try {
      for (const row of loadCachedSessions()) {
        if (row && row.session_id && !sessions.has(row.session_id)) {
          sessions.set(row.session_id, row);
        }
      }
      const data = await api("/qc/demo/pending");
      const items = (data && data.items) || [];
      pendingIds.clear();
      for (const item of items) {
        if (!item || !item.session_id) continue;
        pendingIds.add(item.session_id);
        upsertSession(item);
      }
      renderList();
    } catch (e) {
      renderList();
      showToast(`列表失败：${e.message}`, true);
    }
  }

  function setCheckBanner(msg, isErr) {
    if (!els.checkBanner) return;
    if (!msg) {
      els.checkBanner.hidden = true;
      els.checkBanner.textContent = "";
      els.checkBanner.classList.remove("err");
      return;
    }
    els.checkBanner.hidden = false;
    els.checkBanner.textContent = msg;
    els.checkBanner.classList.toggle("err", !!isErr);
  }

  async function onListRowClick(sessionId) {
    if (!sessionId) return;
    if (checkingIds.has(sessionId)) return;
    const isPending = pendingIds.has(sessionId);
    if (isPending) {
      await runReplay(sessionId);
      return;
    }
    await openDetail(sessionId, { replay: false });
  }

  async function runReplay(sessionId) {
    if (checkingIds.has(sessionId)) return;
    checkingIds.add(sessionId);
    renderList();
    showList(false);
    currentSessionId = sessionId;
    els.detailTitle.textContent = sessionId;
    setCheckBanner("开检中…禁止对本行重复触发");
    els.detailMeta.innerHTML = "";
    els.hitsBody.innerHTML = `<tr><td colspan="5" class="muted">开检中…</td></tr>`;
    hideLangSmith();
    try {
      const replayed = await api(`/qc/replay/${encodeURIComponent(sessionId)}`, {
        method: "POST",
        body: "{}",
      });
      if (replayed && replayed.call_id && String(replayed.call_id) === String(sessionId)) {
        replayed.call_id = null;
      }
      upsertSession({
        session_id: sessionId,
        call_id: replayed && replayed.call_id,
        qc_record_id: replayed && replayed.qc_record_id,
        qc_status: replayed && replayed.qc_status,
        compliance_score: replayed && replayed.compliance_score,
        machine_score: replayed && replayed.compliance_score,
      });
      pendingIds.delete(sessionId);
      await openDetail(sessionId, { replay: false, preload: replayed });
      showToast(`开检完成：${sessionId}`);
    } catch (e) {
      setCheckBanner(`开检失败：${e.message}`, true);
      showToast(`开检失败：${e.message}`, true);
    } finally {
      checkingIds.delete(sessionId);
      renderList();
    }
  }

  function showList(show) {
    els.listPanel.hidden = !show;
    els.detailPanel.hidden = show;
  }

  function hideLangSmith() {
    if (!els.btnLangSmith) return;
    els.btnLangSmith.hidden = true;
    els.btnLangSmith.removeAttribute("href");
  }

  function isHttpUrl(url) {
    return typeof url === "string" && /^https?:\/\//i.test(url);
  }

  function hitCounts(hit) {
    if (hit && typeof hit.score_eligible === "boolean") return hit.score_eligible;
    return String(hit.speaker || "") !== "user";
  }

  function penaltyPoints(hit) {
    if (!hitCounts(hit)) return 0;
    return PENALTY[hit.severity] || 0;
  }

  function penaltyLabel(hit) {
    if (!hitCounts(hit)) return "不计入";
    const n = PENALTY[hit.severity];
    if (!n) return "不计入";
    return `-${n}`;
  }

  function penaltySum(hits) {
    return (hits || []).reduce((sum, hit) => sum + penaltyPoints(hit), 0);
  }

  function clauseText(hit) {
    const snippet = hit && hit.kb_snippet;
    if (snippet) return String(snippet);
    return "无检索条款 / 已回退关键词";
  }

  function machineScoreOf(record, sessionId) {
    const cached = sessions.get(sessionId) || {};
    if (cached.machine_score != null && cached.machine_score !== "") {
      return Number(cached.machine_score);
    }
    return Number(record && record.compliance_score);
  }

  function setReviewButtonsEnabled(enabled) {
    els.btnConfirm.disabled = !enabled;
    els.btnRescoreMode.disabled = !enabled;
    els.btnCloseQc.disabled = !enabled;
  }

  function enterRescoreMode() {
    if (!token()) {
      showToast("请先灌数或粘贴 QC JWT", true);
      return;
    }
    rescoreMode = true;
    els.revisedScore.disabled = false;
    if (els.revisedScore.value === "") {
      els.revisedScore.value = String(machineScoreOf(currentRecord, currentSessionId));
    }
    els.revisedScore.focus();
    els.reviewHint.textContent = "已进入改分。改数字不会写库，点「关闭质检」才生效。";
  }

  function showReview(record) {
    rescoreMode = false;
    if (els.reviewDone) {
      els.reviewDone.hidden = true;
      els.reviewDone.textContent = "";
    }
    const status = record && record.qc_status;
    const score = Number(record && record.compliance_score);
    const hits = (record && record.hits) || [];
    const summed = penaltySum(hits);
    const zeroDeduction = status === "DONE" && (score === 100 || summed === 0);
    if (!record || zeroDeduction) {
      els.reviewBox.hidden = true;
      els.reviewHint.textContent = "零扣分已完成，无需人审。";
      return;
    }
    if (status === "REVIEWED") {
      els.reviewBox.hidden = true;
      const machine = machineScoreOf(record, currentSessionId);
      let text = `本通已人审，分数 ${score}`;
      if (Number.isFinite(machine) && machine !== score) {
        text = `本通已人审，机检 ${machine} → 人审 ${score}`;
      }
      if (els.reviewDone) {
        els.reviewDone.hidden = false;
        els.reviewDone.textContent = text;
      }
      els.reviewHint.textContent = "";
      return;
    }
    if (status !== "PENDING_REVIEW" && !(score < 100)) {
      els.reviewBox.hidden = true;
      els.reviewHint.textContent = "无需人审。";
      return;
    }
    els.reviewBox.hidden = false;
    const machine = machineScoreOf(record, currentSessionId);
    if (els.penaltyTotal) els.penaltyTotal.textContent = summed ? `-${summed}` : "0";
    if (els.machineScoreLabel) els.machineScoreLabel.textContent = String(machine);
    els.revisedScore.value = "";
    els.revisedScore.disabled = true;
    const hasJwt = Boolean(token());
    setReviewButtonsEnabled(hasJwt);
    els.reviewHint.textContent = hasJwt
      ? "认机检分点「扣分正确」。要改分：先点「误报改分」改数字，再点「关闭质检」才写入。"
      : "请先灌数或粘贴 QC JWT，人审按钮不可用。";
  }

  function renderHits(hits) {
    const rows = hits || [];
    if (!rows.length) {
      els.hitsBody.innerHTML = `<tr><td colspan="5" class="muted">无命中</td></tr>`;
      return;
    }
    els.hitsBody.innerHTML = rows
      .map((hit) => {
        const src = SOURCE_LABEL[hit.source] || hit.source || "—";
        const ruleOrWord = hit.rule_id || hit.word || "—";
        return `<tr>
          <td>${escapeHtml(src)}</td>
          <td><code>${escapeHtml(String(ruleOrWord))}</code></td>
          <td>${escapeHtml(hit.evidence_text || "—")}</td>
          <td>${escapeHtml(penaltyLabel(hit))}</td>
          <td>${escapeHtml(clauseText(hit))}</td>
        </tr>`;
      })
      .join("");
  }

  function renderSkipped(rules) {
    const list = rules || [];
    if (!list.length) {
      els.skippedList.innerHTML = `<li class="muted">无跳过规则</li>`;
      return;
    }
    els.skippedList.innerHTML = list
      .map((rid) => `<li><code>${escapeHtml(String(rid))}</code>：本通未审/未适用</li>`)
      .join("");
  }

  function renderMeta(record, sessionId) {
    const callId =
      record.call_id && String(record.call_id) !== String(sessionId) ? record.call_id : "—";
    const status = record.qc_status || "—";
    const result = record.qc_result || "—";
    const machine = machineScoreOf(record, sessionId);
    const finalScore = record.compliance_score;
    const rows = [
      ["session_id", sessionId],
      ["call_id", callId],
      ["qc_record_id", record.qc_record_id || "—"],
      ["compliance_score", finalScore != null ? String(finalScore) : "—"],
    ];
    if (status === "REVIEWED" && Number.isFinite(machine) && Number(finalScore) !== machine) {
      rows.push(["machine_score", String(machine)], ["review_score", String(finalScore)]);
    }
    rows.push(
      ["grade", record.grade || "—"],
      ["risk", record.risk || "—"],
      ["qc_status", STATUS_LABEL[status] || status],
      ["qc_result", RESULT_LABEL[result] || result],
    );
    els.detailMeta.innerHTML = rows
      .map(([k, v]) => `<dt>${escapeHtml(META_LABEL[k] || k)}</dt><dd>${escapeHtml(v)}</dd>`)
      .join("");
  }

  async function openDetail(sessionId, { replay, preload } = {}) {
    currentSessionId = sessionId;
    showList(false);
    els.detailTitle.textContent = sessionId;
    if (replay) setCheckBanner("开检中…");
    else setCheckBanner("");
    hideLangSmith();
    if (els.mcpNote) {
      els.mcpNote.hidden = true;
      els.mcpNote.textContent = "";
    }
    try {
      let record = preload || {};
      try {
        const fromDb = await api(`/qc/records/${encodeURIComponent(sessionId)}`);
        record = { ...preload, ...fromDb };
      } catch (e) {
        if (!preload || !preload.qc_record_id) throw e;
      }
      const cached = sessions.get(sessionId) || {};
      if (!record.call_id) record.call_id = cached.call_id;
      if (record.call_id && String(record.call_id) === String(sessionId)) {
        record.call_id = null;
      }
      const machine =
        cached.machine_score != null
          ? cached.machine_score
          : record.qc_status === "PENDING_REVIEW"
            ? record.compliance_score
            : cached.machine_score;
      currentRecord = record;
      upsertSession({
        session_id: sessionId,
        call_id: record.call_id,
        qc_record_id: record.qc_record_id,
        qc_status: record.qc_status,
        compliance_score: record.compliance_score,
        machine_score: machine,
      });
      renderMeta(record, sessionId);
      renderHits(record.hits);
      renderSkipped(record.skipped_rules);
      showReview(record);
      await loadTrace(sessionId);
    } catch (e) {
      currentRecord = null;
      showToast(`详情失败：${e.message}`, true);
      els.detailMeta.innerHTML = "";
      els.hitsBody.innerHTML = `<tr><td colspan="5" class="muted">${escapeHtml(e.message)}</td></tr>`;
    }
  }

  async function loadTrace(sessionId) {
    if (!els.traceTimeline) return;
    try {
      const data = await api(`/qc/demo/${encodeURIComponent(sessionId)}/trace`);
      const events = (data && data.events) || [];
      const url = data && data.langsmith_url;
      if (els.btnLangSmith) {
        if (isHttpUrl(url)) {
          els.btnLangSmith.hidden = false;
          els.btnLangSmith.href = url;
        } else {
          hideLangSmith();
        }
      }
      if (els.traceHint) {
        els.traceHint.textContent = isHttpUrl(url)
          ? "本地时间线 + 可选 LangSmith 外链"
          : "本地时间线（无 Key / 无 run id / 无模板则隐藏外链）";
      }
      const note = (data && (data.mcp_call_qc_note || data.mcp_call_qc)) || "";
      if (els.mcpNote) {
        if (note) {
          els.mcpNote.hidden = false;
          els.mcpNote.textContent =
            note === "not_called" ? "本通未调用 MCP call_qc" : String(note);
        } else {
          els.mcpNote.hidden = true;
        }
      }
      if (!events.length) {
        els.traceTimeline.innerHTML = `<li class="muted">暂无时间线事件</li>`;
        return;
      }
      els.traceTimeline.innerHTML = events
        .map((ev) => {
          const cat = String(ev.category || "其他");
          return `<li>
            <div class="trace-head">
              <span class="trace-cat ${escapeAttr(cat.toLowerCase())}">${escapeHtml(cat)}</span>
              <span class="trace-name">${escapeHtml(ev.name || "—")}</span>
              <span class="trace-meta">${escapeHtml(ev.status || "")} · ${escapeHtml(String(ev.duration_ms ?? 0))}ms · ${escapeHtml(formatTime(ev.started_at))}</span>
            </div>
            <pre class="trace-summary">${escapeHtml(ev.summary || "")}</pre>
          </li>`;
        })
        .join("");
    } catch (e) {
      els.traceTimeline.innerHTML = `<li class="muted">追踪加载失败：${escapeHtml(e.message)}</li>`;
      hideLangSmith();
    }
  }

  async function seedDemo() {
    els.btnSeed.disabled = true;
    try {
      const data = await api("/qc/demo/seed", { method: "POST", body: "{}" });
      const list = (data && data.sessions) || [];
      for (const row of list) upsertSession(row);
      if (data && data.token) {
        els.jwtToken.value = data.token;
        persistToken();
      }
      const ids = (data && data.session_ids) || list.map((s) => s.session_id);
      showToast(`已灌入 ${ids.length} 条`);
      await loadList();
    } catch (e) {
      showToast(`灌数失败：${e.message}`, true);
    } finally {
      els.btnSeed.disabled = false;
    }
  }

  function readRevisedScore() {
    const raw = els.revisedScore.value;
    if (raw === "" || raw == null) return null;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 0 || n > 100) return null;
    return n;
  }

  async function submitReview(action, bodyExtra) {
    if (!currentRecord || !currentRecord.qc_record_id) {
      showToast("缺少 qc_record_id，无法人审", true);
      return;
    }
    if (!token()) {
      showToast("请先灌数或粘贴 QC JWT", true);
      return;
    }
    try {
      await api(`/qc/reviews/${encodeURIComponent(currentRecord.qc_record_id)}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: JSON.stringify({ action, ...(bodyExtra || {}) }),
      });
      showToast(`人审已提交：${action === "CONFIRM" ? "扣分正确" : "关闭质检"}`);
      await openDetail(currentSessionId, { replay: false });
      renderList();
    } catch (e) {
      showToast(`人审失败：${e.message}`, true);
    }
  }

  function onConfirm() {
    submitReview("CONFIRM");
  }

  function onCloseQc() {
    if (!rescoreMode) {
      showToast("请先点「误报改分」并修改分数，或点「扣分正确」认机检分", true);
      return;
    }
    const score = readRevisedScore();
    if (score == null) {
      showToast("请填写 0–100 整数分数后再关闭质检", true);
      return;
    }
    const machine = machineScoreOf(currentRecord, currentSessionId);
    if (score === machine) {
      showToast("分数仍等于机检分。请改分后再关闭，或点「扣分正确」", true);
      return;
    }
    submitReview("CORRECT", { revised_score: score });
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }
  function formatTime(v) {
    if (!v) return "—";
    try {
      return new Date(v).toLocaleString();
    } catch (_) {
      return String(v);
    }
  }

  els.apiBase.value = params.get("api") || (stored && !isLegacyDefault(stored) ? stored : DEFAULT_API);
  els.jwtToken.value = localStorage.getItem(STORE_TOKEN) || "";
  for (const row of loadCachedSessions()) {
    if (row && row.session_id) sessions.set(row.session_id, row);
  }
  syncLabel();
  els.apiBase.addEventListener("change", () => {
    syncLabel();
    loadList();
  });
  els.jwtToken.addEventListener("change", () => {
    persistToken();
    if (currentRecord) showReview(currentRecord);
  });
  els.btnRefresh.addEventListener("click", loadList);
  els.btnSeed.addEventListener("click", seedDemo);
  if (els.listFilter) els.listFilter.addEventListener("change", renderList);
  els.btnBack.addEventListener("click", () => {
    showList(true);
    currentSessionId = null;
    currentRecord = null;
    rescoreMode = false;
    setCheckBanner("");
    loadList();
  });
  els.btnConfirm.addEventListener("click", onConfirm);
  els.btnRescoreMode.addEventListener("click", enterRescoreMode);
  els.btnCloseQc.addEventListener("click", onCloseQc);

  loadList();
})();
