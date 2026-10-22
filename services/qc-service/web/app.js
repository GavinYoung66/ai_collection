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
    deduction_total: "扣分",
    machine_deduction: "机检扣分",
    review_deduction: "人审扣分",
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
    const v = String(raw || "").replace(/\/$/, "");
    // 相对路径（以 / 开头但无 origin）仅在与后端同域时才成立；
    // 静态 server（8080）或 file:// 协议上相对路径会打到 404/501，自动补后端 origin。
    if (v.startsWith("/") && !/^https?:\/\//.test(v)) {
      return `http://localhost:8003${v}`;
    }
    return v;
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
        const score = row.deduction_total != null ? `-${row.deduction_total}` : "—";
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
        deduction_total: replayed && replayed.deduction_total,
        compliance_score: replayed && replayed.compliance_score,
        machine_deduction: replayed && replayed.deduction_total,
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
    if (hit && hit.points != null) return Number(hit.points) || 0;
    return PENALTY[hit.severity] || 0;
  }

  function penaltyLabel(hit) {
    if (!hitCounts(hit)) return "不计入";
    const n = penaltyPoints(hit);
    if (!n) return "不计入";
    return `-${n}`;
  }

  function penaltySum(hits) {
    return (hits || []).reduce((sum, hit) => sum + penaltyPoints(hit), 0);
  }

  function clauseText(hit) {
    // V1.18：优先展示后端静态制度条款（policy_clause + policy_text）；
    // 纯 AC hit 无 rule_id 由 __ac_default__ 兜底填充；kb_snippet（RAG 片段）次选。
    if (hit && (hit.policy_clause || hit.policy_text)) {
      const parts = [];
      if (hit.policy_clause) parts.push(String(hit.policy_clause));
      if (hit.policy_text) parts.push(String(hit.policy_text));
      return parts.join(" ");
    }
    const snippet = hit && hit.kb_snippet;
    if (snippet) return String(snippet);
    return "无制度依据（已回退关键词）";
  }

  function machineDeductionOf(record, sessionId) {
    const cached = sessions.get(sessionId) || {};
    if (cached.machine_deduction != null && cached.machine_deduction !== "") {
      return Number(cached.machine_deduction);
    }
    return Number(record && record.deduction_total);
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
      els.revisedScore.value = String(machineDeductionOf(currentRecord, currentSessionId) || 0);
    }
    els.revisedScore.focus();
    els.reviewHint.textContent = "已进入改扣分。填整数 >=0 不会写库，点「提交人审」才生效。";
  }

  function showReview(record) {
    rescoreMode = false;
    if (els.reviewDone) {
      els.reviewDone.hidden = true;
      els.reviewDone.textContent = "";
    }
    const status = record && record.qc_status;
    const deduction = Number(record && record.deduction_total);
    const hits = (record && record.hits) || [];
    const summed = penaltySum(hits);
    const zeroDeduction = deduction === 0 || (status === "DONE" && summed === 0);
    if (!record || zeroDeduction) {
      els.reviewBox.hidden = true;
      els.reviewHint.textContent = "零扣分已完成，无需人审。";
      return;
    }
    if (status === "REVIEWED") {
      els.reviewBox.hidden = true;
      const machine = machineDeductionOf(record, currentSessionId);
      let text = `本通已人审，扣分 -${deduction}`;
      if (Number.isFinite(machine) && machine !== deduction) {
        text = `本通已人审，机检扣分 -${machine} → 人审扣分 -${deduction}`;
      }
      if (els.reviewDone) {
        els.reviewDone.hidden = false;
        els.reviewDone.textContent = text;
      }
      els.reviewHint.textContent = "";
      return;
    }
    if (status !== "PENDING_REVIEW") {
      els.reviewBox.hidden = true;
      els.reviewHint.textContent = "无需人审。";
      return;
    }
    els.reviewBox.hidden = false;
    const machine = machineDeductionOf(record, currentSessionId);
    if (els.penaltyTotal) els.penaltyTotal.textContent = deduction ? `-${deduction}` : "0";
    if (els.machineScoreLabel) els.machineScoreLabel.textContent = `-${machine}`;
    els.revisedScore.value = "";
    els.revisedScore.disabled = true;
    const hasJwt = Boolean(token());
    setReviewButtonsEnabled(hasJwt);
    els.reviewHint.textContent = hasJwt
      ? "认机检扣分点「扣分正确」。要改扣分：先点「误报改扣分」填整数，再点「提交人审」才写入。"
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
    const machine = machineDeductionOf(record, sessionId);
    const finalDeduction = record.deduction_total;
    const rows = [
      ["session_id", sessionId],
      ["call_id", callId],
      ["qc_record_id", record.qc_record_id || "—"],
      ["deduction_total", finalDeduction != null ? `-${finalDeduction}` : "—"],
    ];
    if (status === "REVIEWED" && Number.isFinite(machine) && Number(finalDeduction) !== machine) {
      rows.push(["machine_deduction", `-${machine}`], ["review_deduction", `-${finalDeduction}`]);
    }
    rows.push(
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
        cached.machine_deduction != null
          ? cached.machine_deduction
          : record.qc_status === "PENDING_REVIEW"
            ? record.deduction_total
            : cached.machine_deduction;
      currentRecord = record;
      upsertSession({
        session_id: sessionId,
        call_id: record.call_id,
        qc_record_id: record.qc_record_id,
        qc_status: record.qc_status,
        deduction_total: record.deduction_total,
        compliance_score: record.compliance_score,
        machine_deduction: machine,
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

  function readRevisedDeduction() {
    const raw = els.revisedScore.value;
    if (raw === "" || raw == null) return null;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 0) return null;
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
      showToast("请先点「误报改扣分」并填写扣分值，或点「扣分正确」认机检扣分", true);
      return;
    }
    const deduction = readRevisedDeduction();
    if (deduction == null) {
      showToast("请填写 >=0 整数扣分后再提交", true);
      return;
    }
    const machine = machineDeductionOf(currentRecord, currentSessionId);
    if (deduction === machine) {
      showToast("扣分仍等于机检扣分。请改值后再提交，或点「扣分正确」", true);
      return;
    }
    submitReview("CORRECT", { revised_deduction: deduction });
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
