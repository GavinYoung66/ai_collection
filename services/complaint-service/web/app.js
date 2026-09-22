(() => {
  const DEFAULT_API = "/api/v1";
  const LEGACY_API_BASES = new Set([
    "http://localhost:8006",
    "http://127.0.0.1:8006",
  ]);
  const params = new URLSearchParams(window.location.search);
  const stored = localStorage.getItem("complaint_api_base");

  const els = {
    apiBase: document.getElementById("apiBase"),
    apiLabel: document.getElementById("apiLabel"),
    btnRefresh: document.getElementById("btnRefresh"),
    btnSeed: document.getElementById("btnSeed"),
    listBody: document.getElementById("listBody"),
    listPanel: document.getElementById("listPanel"),
    detailPanel: document.getElementById("detailPanel"),
    detailTitle: document.getElementById("detailTitle"),
    detailMeta: document.getElementById("detailMeta"),
    detailText: document.getElementById("detailText"),
    detailDraft: document.getElementById("detailDraft"),
    detailLogs: document.getElementById("detailLogs"),
    btnBack: document.getElementById("btnBack"),
    btnConfirmVerdict: document.getElementById("btnConfirmVerdict"),
    btnScheduleCallback: document.getElementById("btnScheduleCallback"),
    btnReject: document.getElementById("btnReject"),
    btnRejudge: document.getElementById("btnRejudge"),
    btnClose: document.getElementById("btnClose"),
    btnToggleLogs: document.getElementById("btnToggleLogs"),
    reviewer: document.getElementById("reviewer"),
    comment: document.getElementById("comment"),
    overrideReason: document.getElementById("overrideReason"),
    verdictFieldset: document.getElementById("verdictFieldset"),
    toast: document.getElementById("toast"),
    statusStrip: document.getElementById("statusStrip"),
    judgeBanner: document.getElementById("judgeBanner"),
    pendingBanner: document.getElementById("pendingBanner"),
    traceTimeline: document.getElementById("traceTimeline"),
    traceHint: document.getElementById("traceHint"),
    btnLangSmith: document.getElementById("btnLangSmith"),
    evidenceList: document.getElementById("evidenceList"),
    evidenceHint: document.getElementById("evidenceHint"),
  };

  let currentId = null;
  let currentStatus = null;
  let currentAiVerdict = null;
  let currentTraceEvents = [];
  /** @type {Set<string>} */
  const judgingIds = new Set();

  const ESTABLISHMENT = new Set([
    "agent_violation",
    "user_misunderstanding",
    "compliant_but_poor_experience",
  ]);

  const VERDICT_LABEL = {
    agent_violation: "坐席违规",
    user_misunderstanding: "用户误解",
    compliant_but_poor_experience: "体验差（合规）",
    pending: "待定 (pending)",
  };

  /** design D4 — 生命周期状态条 */
  const STATUS_STEPS = [
    { id: "intake", label: "接单" },
    { id: "evidence", label: "取证" },
    { id: "reflection", label: "反思" },
    { id: "judging", label: "三路研判" },
    { id: "verdict", label: "判责" },
    { id: "draft", label: "答辩" },
    { id: "review", label: "待人审" },
    { id: "callback", label: "待回电" },
    { id: "resolved", label: "已处置" },
    { id: "closed", label: "已关单" },
  ];

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

  function syncLabel() {
    els.apiLabel.textContent = apiPrefix();
    localStorage.setItem("complaint_api_base", apiBase());
  }

  function showToast(msg, isErr) {
    els.toast.hidden = false;
    els.toast.textContent = msg;
    els.toast.classList.toggle("err", !!isErr);
  }

  async function api(path, options = {}) {
    const url = `${apiPrefix()}${path}`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let body = null;
    try {
      body = await res.json();
    } catch (_) {
      body = null;
    }
    if (!res.ok) {
      const msg = (body && (body.message || body.detail)) || res.statusText;
      throw new Error(`${res.status} ${msg}`);
    }
    if (body && typeof body.code === "number" && body.code !== 0) {
      throw new Error(body.message || `API code ${body.code}`);
    }
    if (body && typeof body.code === "string" && body.code !== "0") {
      throw new Error(body.message || `API code ${body.code}`);
    }
    return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
  }

  function trunc(s, n = 48) {
    const t = (s || "").replace(/\s+/g, " ").trim();
    return t.length > n ? `${t.slice(0, n)}…` : t || "—";
  }

  function canRejudge(st) {
    // 退回取证 / 反思重试后也必须能再跑，否则人审按钮全灰会卡死
    return (
      st === "pending" ||
      st === "evidence_gathering" ||
      st === "reflection_retry" ||
      st === "pending_review" ||
      st === "pending_senior_review"
    );
  }

  function setPendingBanner(aiVerdict, status) {
    if (!els.pendingBanner) return;
    const isPendingAi = aiVerdict === "pending";
    const inReview =
      status === "pending_review" || status === "pending_senior_review";
    if (els.btnRejudge) els.btnRejudge.classList.remove("emphasize");
    if (els.btnReject) els.btnReject.classList.remove("emphasize");
    if (isPendingAi && inReview) {
      els.pendingBanner.hidden = false;
      els.pendingBanner.innerHTML =
        "<strong>AI 结论为 pending，证据尚不足以下定论。</strong>" +
        "<ul class=\"pending-actions\">" +
        "<li>请优先点「<strong>重新研判</strong>」或「<strong>退回 AI 重跑</strong>」再跑一轮。</li>" +
        "<li>补证指引：核对<strong>催收录音</strong>、<strong>催收电话记录</strong>、<strong>短信/触达记录</strong>后再定责。</li>" +
        "<li>若证据已足够，可改判为三类成立结论之一（须填改判原因）；<strong>禁止无成立结论关单</strong>。</li>" +
        "</ul>";
      if (els.btnRejudge && !els.btnRejudge.disabled) {
        els.btnRejudge.classList.add("emphasize");
      }
      if (els.btnReject && !els.btnReject.disabled) {
        els.btnReject.classList.add("emphasize");
      }
    } else if (status === "evidence_gathering" || status === "reflection_retry") {
      els.pendingBanner.hidden = false;
      els.pendingBanner.innerHTML =
        "工单已退回<strong>取证/反思</strong>，当前还不能定责或关单。" +
        "请点「<strong>重新研判</strong>」再跑一轮 AI，进入待人审后再确认成立性。";
      if (els.btnRejudge && !els.btnRejudge.disabled) {
        els.btnRejudge.classList.add("emphasize");
      }
    } else if (status === "pending_callback") {
      els.pendingBanner.hidden = false;
      els.pendingBanner.textContent =
        "已定责，待回电：请点击「确认回电安排」发布 callback.requested；未回电不可关单。";
    } else {
      els.pendingBanner.hidden = true;
      els.pendingBanner.textContent = "";
    }
  }

  function selectedEstablishment() {
    const el = document.querySelector('input[name="establishment"]:checked');
    return el ? el.value : null;
  }

  function setEstablishment(value) {
    const radios = document.querySelectorAll('input[name="establishment"]');
    radios.forEach((r) => {
      r.checked = ESTABLISHMENT.has(value) && r.value === value;
    });
  }

  function renderEvidenceList(detail) {
    if (!els.evidenceList) return;
    const disp = detail.disposition_verdict;
    const items = detail.ordered_evidence || [];
    const refs = Array.isArray(detail.evidence_refs) ? detail.evidence_refs : [];
    const refsHtml = refs.length
      ? `<li class="ev-refs"><strong>evidence_refs (doc_id)</strong>: ${escapeHtml(
          refs.join(", ")
        )}</li>`
      : `<li class="muted">暂无 evidence_refs / doc_id（stretch COMP-03）。</li>`;

    if (disp !== "agent_violation") {
      els.evidenceList.innerHTML =
        refsHtml +
        `<li class="muted">仅在人审定责为「坐席违规」后展示有序证据清单。</li>`;
      if (els.evidenceHint) {
        els.evidenceHint.textContent =
          "人审确认「坐席违规」后展示有序清单；evidence_refs/doc_id 可随时可见（stretch）。";
      }
      return;
    }
    if (!items.length) {
      els.evidenceList.innerHTML =
        refsHtml +
        `<li class="muted">暂无结构化证据（空态）。仍可安排回电；质检事件将携带空 evidence 数组。</li>`;
      return;
    }
    if (els.evidenceHint) {
      els.evidenceHint.textContent = `共 ${items.length} 条（按时间升序）；无处罚档位。evidence_refs=${refs.length}`;
    }
    els.evidenceList.innerHTML =
      refsHtml +
      items
      .map((it) => {
        const ord = it.ord != null ? it.ord : "";
        return `<li>
          <div class="ev-head">
            <span class="ev-ord">#${escapeHtml(String(ord))}</span>
            <span class="ev-cat">${escapeHtml(it.category || "其他")}</span>
            <span class="ev-name">${escapeHtml(it.name || "—")}</span>
            <span class="ev-ord">${escapeHtml(formatTime(it.started_at))}</span>
          </div>
          <pre class="ev-summary">${escapeHtml(it.summary || "")}</pre>
        </li>`;
      })
      .join("");
  }

  function prefillsFromDetail(detail) {
    currentAiVerdict =
      detail.ai_verdict != null && detail.ai_verdict !== undefined
        ? detail.ai_verdict
        : detail.verdict || null;
    let pref = null;
    if (detail.disposition_verdict && ESTABLISHMENT.has(detail.disposition_verdict)) {
      pref = detail.disposition_verdict;
    } else if (ESTABLISHMENT.has(detail.verdict)) {
      pref = detail.verdict;
    }
    setEstablishment(pref);
    if (els.overrideReason) {
      els.overrideReason.value = detail.disposition_reason || "";
    }
  }

  function displayStatus(id, st) {
    if (judgingIds.has(id)) return "研判中…";
    return st || "—";
  }

  async function loadList() {
    syncLabel();
    els.listBody.innerHTML = `<tr><td colspan="5" class="muted">加载中…</td></tr>`;
    try {
      const data = await api("/complaints?page=1&page_size=50");
      const items = ((data && data.items) || []).slice().sort((a, b) => {
        const ta = new Date(a.created_at || 0).getTime();
        const tb = new Date(b.created_at || 0).getTime();
        return tb - ta;
      });
      if (!items.length) {
        els.listBody.innerHTML = `<tr><td colspan="5" class="muted">暂无工单</td></tr>`;
        return;
      }
      els.listBody.innerHTML = items
        .map((it) => {
          const id = it.complaint_id;
          const st = it.task_status || "";
          const judging = judgingIds.has(id);
          return `<tr data-id="${escapeAttr(id)}" data-status="${escapeAttr(st)}" class="${judging ? "judging" : ""}">
            <td><code>${escapeHtml(id)}</code></td>
            <td class="trunc" title="${escapeAttr(it.complaint_text || "")}">${escapeHtml(trunc(it.complaint_text))}</td>
            <td class="status-cell">${escapeHtml(displayStatus(id, st))}</td>
            <td>${escapeHtml(it.verdict || "—")}</td>
            <td>${escapeHtml(formatTime(it.created_at))}</td>
          </tr>`;
        })
        .join("");
      els.listBody.querySelectorAll("tr[data-id]").forEach((tr) => {
        tr.addEventListener("click", () => {
          const id = tr.getAttribute("data-id");
          const st = tr.getAttribute("data-status") || "";
          onListRowClick(id, st);
        });
      });
    } catch (e) {
      els.listBody.innerHTML = `<tr><td colspan="5" class="muted">加载失败：${escapeHtml(e.message)}</td></tr>`;
      showToast(`列表失败：${e.message}`, true);
    }
  }

  async function onListRowClick(id, status) {
    if (!id) return;
    if (judgingIds.has(id)) {
      // 研判中仍可点进详情看等待态（勿用 pointer-events:none 锁死行）
      await openDetail(id, { judging: true });
      return;
    }
    if (status === "pending") {
      // 先打开详情，再跑真图（避免卡在列表）
      await openDetail(id, { judging: true });
      // 不 await：返回列表后仍可点其它行；本单在 judgingIds 防连点
      void runJudge(id, { refreshDetail: true });
      return;
    }
    await openDetail(id);
  }

  function setJudgeBanner(msg, isErr) {
    if (!els.judgeBanner) return;
    if (!msg) {
      els.judgeBanner.hidden = true;
      els.judgeBanner.textContent = "";
      els.judgeBanner.classList.remove("err");
      return;
    }
    els.judgeBanner.hidden = false;
    els.judgeBanner.textContent = msg;
    els.judgeBanner.classList.toggle("err", !!isErr);
  }

  async function runJudge(id, { refreshDetail } = {}) {
    if (!id) return;
    if (judgingIds.has(id)) return;
    judgingIds.add(id);
    if (els.btnRejudge) els.btnRejudge.disabled = true;
    const row = els.listBody.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
    if (row) {
      row.classList.add("judging");
      const cell = row.querySelector(".status-cell");
      if (cell) cell.textContent = "研判中…";
    }
    if (currentId === id) {
      setJudgeBanner("研判进行中（真图可能需数十秒）…完成后自动刷新本页");
    }
    showToast(`研判中：${id}（真图可能需数十秒）…`);
    try {
      await api(`/complaints/${encodeURIComponent(id)}/judge`, {
        method: "POST",
        body: JSON.stringify({ run_graph: true }),
      });
      showToast(`研判完成：${id}`);
      judgingIds.delete(id);
      if ((refreshDetail || currentId === id) && currentId === id) {
        await openDetail(id);
      }
      if (!els.listPanel.hidden) await loadList();
    } catch (e) {
      showToast(`研判失败：${e.message}`, true);
      judgingIds.delete(id);
      if (currentId === id) {
        setJudgeBanner(`研判失败：${e.message}`, true);
        try {
          await openDetail(id);
        } catch (_) {
          /* ignore */
        }
      }
      if (!els.listPanel.hidden) await loadList();
    } finally {
      judgingIds.delete(id);
      updateActionButtons();
    }
  }

  async function openDetail(id, { judging } = {}) {
    currentId = id;
    currentTraceEvents = [];
    els.listPanel.hidden = true;
    els.detailPanel.hidden = false;
    els.detailTitle.textContent = id;
    els.detailMeta.innerHTML = "";
    els.detailText.textContent = "加载中…";
    els.detailDraft.textContent = "";
    els.detailLogs.hidden = true;
    els.detailLogs.textContent = "加载中…";
    if (judging || judgingIds.has(id)) {
      setJudgeBanner("研判进行中（真图可能需数十秒）…完成后自动刷新本页");
    } else {
      setJudgeBanner("");
    }
    if (els.traceTimeline) {
      els.traceTimeline.innerHTML = `<li class="muted">加载追踪…</li>`;
    }
    if (els.btnLangSmith) {
      els.btnLangSmith.hidden = true;
      els.btnLangSmith.removeAttribute("href");
    }
    try {
      const detail = await api(`/complaints/${encodeURIComponent(id)}`);
      currentStatus = detail.task_status;
      const aiShow = detail.ai_verdict || detail.verdict || "—";
      const statusLabel =
        judgingIds.has(id) && detail.task_status === "pending"
          ? "研判中…"
          : detail.task_status;
      els.detailMeta.innerHTML = [
        ["状态", statusLabel],
        ["AI 初选", VERDICT_LABEL[aiShow] || aiShow],
        [
          "人审定责",
          detail.disposition_verdict
            ? VERDICT_LABEL[detail.disposition_verdict] || detail.disposition_verdict
            : "—",
        ],
        ["回电安排", detail.callback_scheduled ? "已安排" : "未安排"],
        ["置信度", detail.confidence != null ? String(detail.confidence) : "—"],
        [
          "证据 doc_id",
          Array.isArray(detail.evidence_refs) && detail.evidence_refs.length
            ? detail.evidence_refs.join(", ")
            : "—",
        ],
        ["更新", formatTime(detail.updated_at)],
      ]
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`)
        .join("");
      els.detailText.textContent = detail.complaint_text || "—";
      els.detailDraft.textContent = detail.response_draft || "—";
      prefillsFromDetail(detail);
      renderEvidenceList(detail);
      updateActionButtons();
      setPendingBanner(currentAiVerdict, currentStatus);
      if (!judgingIds.has(id)) {
        await loadTrace(id);
        renderStatusStrip(currentStatus, currentTraceEvents);
        setJudgeBanner("");
        showToast(`已打开 ${id}`);
      } else {
        renderStatusStrip(currentStatus, []);
        if (els.traceTimeline) {
          els.traceTimeline.innerHTML = `<li class="muted">研判完成后自动加载链路追踪…</li>`;
        }
      }
    } catch (e) {
      showToast(`详情失败：${e.message}`, true);
      renderStatusStrip(currentStatus, []);
    }
  }

  async function loadTrace(id) {
    if (!els.traceTimeline) return;
    try {
      const data = await api(`/complaints/${encodeURIComponent(id)}/trace`);
      const events = (data && data.events) || [];
      currentTraceEvents = events;
      const url = data && data.langsmith_url;
      if (els.btnLangSmith) {
        if (url) {
          els.btnLangSmith.hidden = false;
          els.btnLangSmith.href = url;
        } else {
          els.btnLangSmith.hidden = true;
          els.btnLangSmith.removeAttribute("href");
        }
      }
      if (els.traceHint) {
        els.traceHint.textContent = url
          ? "本地时间线 + 可选 LangSmith 外链"
          : "本地时间线（无外部 Trace 链接；外链需 KEY + LANGSMITH_TRACE_URL_TEMPLATE）";
      }
      if (!events.length) {
        els.traceTimeline.innerHTML = `<li class="muted">暂无落盘事件（对 pending 点选研判后可见）</li>`;
        return;
      }
      els.traceTimeline.innerHTML = events
        .map((ev) => {
          const cat = String(ev.category || "其他");
          const catClass = cat.toLowerCase();
          return `<li>
            <div class="trace-head">
              <span class="trace-cat ${escapeAttr(catClass)}">${escapeHtml(cat)}</span>
              <span class="trace-name">${escapeHtml(ev.name || "—")}</span>
              <span class="trace-meta">${escapeHtml(ev.status || "")} · ${escapeHtml(String(ev.duration_ms ?? 0))}ms · ${escapeHtml(formatTime(ev.started_at))}</span>
            </div>
            <pre class="trace-summary">${escapeHtml(ev.summary || "")}</pre>
          </li>`;
        })
        .join("");
    } catch (e) {
      currentTraceEvents = [];
      els.traceTimeline.innerHTML = `<li class="muted">追踪加载失败：${escapeHtml(e.message)}</li>`;
      if (els.btnLangSmith) els.btnLangSmith.hidden = true;
      if (els.traceHint) els.traceHint.textContent = "本地时间线（加载失败时仍不依赖 LangSmith）";
    }
  }

  /** @param {string|null} status @param {Array} events */
  function renderStatusStrip(status, events) {
    if (!els.statusStrip) return;
    const st = status || "";
    const names = (events || [])
      .filter((e) => String(e.category || "").toLowerCase() === "agent")
      .map((e) => String(e.name || ""));

    const has = (substr) => names.some((n) => n.includes(substr));
    const afterReview = [
      "pending_callback",
      "resolved",
      "closed",
    ];
    const reviewOrLater = [
      "pending_review",
      "pending_senior_review",
      ...afterReview,
    ];
    const reached = {
      intake: true,
      evidence:
        st === "evidence_gathering" ||
        has("evidence_") ||
        ["reflection_retry", "judging", "draft_generated", ...reviewOrLater].includes(st),
      reflection:
        st === "reflection_retry" ||
        has("reflection") ||
        ["judging", "draft_generated", ...reviewOrLater].includes(st),
      judging:
        st === "judging" ||
        has("fact") ||
        has("compliance") ||
        has("user_demand") ||
        has("judgment_fanout") ||
        ["draft_generated", ...reviewOrLater].includes(st),
      verdict:
        has("verdict") ||
        ["draft_generated", ...reviewOrLater].includes(st),
      draft:
        st === "draft_generated" ||
        has("generate_package") ||
        reviewOrLater.includes(st),
      review:
        st === "pending_review" ||
        st === "pending_senior_review" ||
        st === "pending_callback" ||
        ["resolved", "closed"].includes(st),
      callback:
        st === "pending_callback" || st === "resolved" || st === "closed",
      resolved: st === "resolved" || st === "closed",
      closed: st === "closed",
    };

    let currentIdStep = "intake";
    if (st === "closed") currentIdStep = "closed";
    else if (st === "resolved") currentIdStep = "resolved";
    else if (st === "pending_callback") currentIdStep = "callback";
    else if (st === "pending_review" || st === "pending_senior_review") currentIdStep = "review";
    else if (st === "draft_generated") currentIdStep = "draft";
    else if (st === "judging") currentIdStep = "judging";
    else if (st === "reflection_retry") currentIdStep = "reflection";
    else if (st === "evidence_gathering") currentIdStep = "evidence";
    else if (st === "pending") currentIdStep = "intake";
    else if (reached.review) currentIdStep = "review";

    els.statusStrip.innerHTML = STATUS_STEPS.map((step) => {
      const isCurrent = step.id === currentIdStep;
      const isDone = !isCurrent && !!reached[step.id];
      const cls = isCurrent ? "current" : isDone ? "done" : "";
      return `<li class="${cls}">${escapeHtml(step.label)}</li>`;
    }).join("");
  }

  function updateActionButtons() {
    const st = currentStatus || "";
    const closed = st === "closed";
    const canDisposition =
      !closed && (st === "pending_review" || st === "pending_senior_review");
    const canCallback = !closed && st === "pending_callback";
    const canClose = !closed && st === "resolved";
    const rejudgeOk =
      !!currentId && canRejudge(st) && !judgingIds.has(currentId);
    if (els.btnConfirmVerdict) els.btnConfirmVerdict.disabled = !canDisposition;
    if (els.btnScheduleCallback) els.btnScheduleCallback.disabled = !canCallback;
    if (els.verdictFieldset) {
      els.verdictFieldset.disabled = !canDisposition;
    }
    if (els.overrideReason) els.overrideReason.disabled = !canDisposition;
    els.btnReject.disabled = !canDisposition;
    els.btnClose.disabled = !canClose;
    if (els.btnRejudge) els.btnRejudge.disabled = !rejudgeOk;
  }

  async function seedDemo() {
    if (!els.btnSeed) return;
    els.btnSeed.disabled = true;
    try {
      const data = await api("/complaints/demo/seed", { method: "POST", body: "{}" });
      const ids = (data && data.complaint_ids) || [];
      showToast(`已模拟推送 ${ids.length} 条：${ids.join("、")}`);
      await loadList();
    } catch (e) {
      showToast(`推送失败：${e.message}`, true);
    } finally {
      els.btnSeed.disabled = false;
    }
  }

  async function loadLogs() {
    if (!currentId) return;
    try {
      const data = await api(`/complaints/${encodeURIComponent(currentId)}/agent-logs`);
      const logs = (data && data.logs) || [];
      els.detailLogs.textContent = logs.length
        ? logs
            .map(
              (l, i) =>
                `#${i + 1} ${l.node_name}\n  in:  ${l.input_summary}\n  out: ${l.output_summary}\n  at:  ${formatTime(l.started_at)} (${l.duration_ms}ms)`
            )
            .join("\n\n")
        : "(empty)";
    } catch (e) {
      els.detailLogs.textContent = `加载失败：${e.message}`;
    }
  }

  async function doConfirmVerdict() {
    if (!currentId) return;
    const establishment = selectedEstablishment();
    if (!establishment || !ESTABLISHMENT.has(establishment)) {
      showToast("请选择成立性：坐席违规 / 用户误解 / 体验差", true);
      return;
    }
    const ai = currentAiVerdict;
    const reason = (els.overrideReason && els.overrideReason.value) || "";
    if (ai !== establishment && !String(reason).trim()) {
      showToast("与 AI 初选不一致时必须填写改判原因", true);
      return;
    }
    try {
      const data = await api(
        `/complaints/${encodeURIComponent(currentId)}/disposition/verdict`,
        {
          method: "PUT",
          body: JSON.stringify({
            establishment,
            reason: String(reason).trim() || null,
            reviewer: els.reviewer.value || "operator",
            comment: els.comment.value || null,
          }),
        }
      );
      currentStatus = data.task_status;
      showToast(`定责 → ${currentStatus}`);
      await openDetail(currentId);
    } catch (e) {
      showToast(`定责失败：${e.message}`, true);
    }
  }

  async function doScheduleCallback() {
    if (!currentId) return;
    try {
      const data = await api(
        `/complaints/${encodeURIComponent(currentId)}/disposition/callback`,
        {
          method: "PUT",
          body: JSON.stringify({
            reviewer: els.reviewer.value || "operator",
            note: els.comment.value || null,
          }),
        }
      );
      currentStatus = data.task_status;
      showToast(`回电已安排 → ${currentStatus}`);
      await openDetail(currentId);
    } catch (e) {
      showToast(`回电安排失败：${e.message}`, true);
    }
  }

  async function doRejectRejudge() {
    if (!currentId) return;
    const body = {
      review_action: "reject_rejudge",
      reviewer: els.reviewer.value || "operator",
      comment: els.comment.value || null,
      reason: "wrong_verdict",
    };
    try {
      const data = await api(`/complaints/${encodeURIComponent(currentId)}/review`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      currentStatus = data.task_status;
      showToast(`退回 AI 重跑 → ${currentStatus}，正在重新研判…`);
      await openDetail(currentId);
      if (canRejudge(currentStatus)) {
        await runJudge(currentId, { refreshDetail: true });
      }
    } catch (e) {
      showToast(`退回失败：${e.message}`, true);
    }
  }

  async function doClose() {
    if (!currentId) return;
    try {
      const data = await api(`/complaints/${encodeURIComponent(currentId)}/close`, {
        method: "PUT",
        body: JSON.stringify({
          closer: els.reviewer.value || "operator",
          reason: "v2_ui_close",
        }),
      });
      currentStatus = data.task_status;
      showToast(`关单 → ${currentStatus}`);
      await openDetail(currentId);
    } catch (e) {
      showToast(`关单失败：${e.message}`, true);
    }
  }

  async function doRejudge() {
    if (!currentId) return;
    if (!canRejudge(currentStatus)) {
      showToast("当前状态不可重新研判", true);
      return;
    }
    await runJudge(currentId, { refreshDetail: true });
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
  syncLabel();
  els.apiBase.addEventListener("change", () => {
    syncLabel();
    loadList();
  });
  els.btnRefresh.addEventListener("click", loadList);
  if (els.btnSeed) els.btnSeed.addEventListener("click", seedDemo);
  els.btnBack.addEventListener("click", () => {
    els.detailPanel.hidden = true;
    els.listPanel.hidden = false;
    currentId = null;
    currentStatus = null;
    setJudgeBanner("");
    loadList();
  });
  els.btnConfirmVerdict.addEventListener("click", doConfirmVerdict);
  if (els.btnScheduleCallback) {
    els.btnScheduleCallback.addEventListener("click", doScheduleCallback);
  }
  els.btnReject.addEventListener("click", doRejectRejudge);
  els.btnClose.addEventListener("click", doClose);
  if (els.btnRejudge) els.btnRejudge.addEventListener("click", doRejudge);
  els.btnToggleLogs.addEventListener("click", async () => {
    const open = els.detailLogs.hidden;
    els.detailLogs.hidden = !open;
    els.btnToggleLogs.textContent = open ? "Agent logs ▴" : "Agent logs ▾";
    if (open) await loadLogs();
  });

  loadList();
})();
