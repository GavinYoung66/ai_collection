"""Gradio 工作台展示层：分类文案、徽章、召回卡片、文件预览解析。不含检索/入库业务。"""
from __future__ import annotations

import base64
import html
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

LLM_SKIP_REASONS = {
    "KB_LLM_ANSWER_ENABLED=false": "后端 LLM 开关未开启（KB_LLM_ANSWER_ENABLED=false）",
    "answer_not_requested": "未勾选「生成 LLM 回答」",
    "empty_context": "检索结果为空，无上下文可生成",
    "circuit_break_open": "LLM 熔断中（连续失败），请稍后重试",
    "pre_redline_blocked": "召回内容命中合规红线，已拦截 LLM 生成",
    "post_redline_blocked": "LLM 回答命中合规红线，已丢弃",
    "search_fallback": "检索触发兜底（相似度/重排分不足）",
    "no_results": "无检索结果",
}

CATEGORY_CODE_TO_ZH = {
    "regulation": "制度法规",
    "script": "催收话术",
    "case": "案例",
    "other": "其他",
}
CATEGORY_ZH_TO_CODE = {v: k for k, v in CATEGORY_CODE_TO_ZH.items()}
CATEGORY_ZH_CHOICES = list(CATEGORY_CODE_TO_ZH.values())
CATEGORY_SEARCH_CHOICES = ["全部"] + CATEGORY_ZH_CHOICES
STATUS_CHOICES = ["全部", "active", "processing", "failed", "archived"]

CHUNK_TABLE_HEADERS = [
    "content", "title", "doc_id", "category", "chunk_index", "RRF", "rerank", "source",
]
DOC_TABLE_HEADERS = ["标题", "分类", "status", "stage", "chunks", "创建时间", "doc_id"]

PREVIEW_CHARS = 800
RAW_DUMP_MAX = 80_000
PDF_PREVIEW_MAX = 8 * 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
TEXT_EXTS = {".txt", ".md"}
NO_EMBED_PREVIEW_EXTS = {".pptx", ".mp3", ".wav", ".m4a", ".amr", ".wma"}


def category_zh(code: str | None) -> str:
    if not code:
        return "-"
    return CATEGORY_CODE_TO_ZH.get(code, code)


def category_code(zh: str | None) -> str | None:
    if not zh or zh == "全部":
        return None
    return CATEGORY_ZH_TO_CODE.get(zh, zh if zh in CATEGORY_CODE_TO_ZH else None)


def human_skip(reason: str | None) -> str:
    if not reason:
        return "-"
    if reason.startswith("provider_error"):
        return f"LLM Provider 调用失败（{reason}）"
    return LLM_SKIP_REASONS.get(reason, reason)


def is_fallback(body: dict) -> bool:
    results = body.get("results") or []
    skip = (body.get("llm_meta") or {}).get("skip_reason")
    return skip == "search_fallback" or (not results and bool(body.get("message")))


def escape(text: str | None) -> str:
    return html.escape(text or "", quote=True)


def extract_step_ms(trace: dict | None, *names: str) -> str:
    if not trace:
        return "-"
    wanted = set(names)
    for step in trace.get("steps") or []:
        if step.get("name") in wanted:
            return str(step.get("latency_ms", "-"))
    return "-"


def extract_step_ms_int(trace: dict | None, *names: str) -> int | None:
    raw = extract_step_ms(trace, *names)
    if raw in (None, "-", ""):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def extract_rewritten_query(body: dict) -> str | None:
    """仅从响应/trace 已有字段读取改写结果，缺失则返回 None（不臆造）。"""
    for key in ("query_rewritten", "rewritten_query"):
        val = body.get(key)
        if val:
            return str(val)
    trace = body.get("trace") or {}
    for key in ("query_rewritten", "rewritten_query"):
        val = (trace.get("summary") or {}).get(key) or trace.get(key)
        if val:
            return str(val)
    for step in trace.get("steps") or []:
        name = str(step.get("name") or "")
        for key in ("rewritten_query", "query_rewritten", "rewritten"):
            if step.get(key):
                return str(step[key])
        if "rewrite" in name.lower():
            for key in ("output", "query"):
                if step.get(key):
                    return str(step[key])
    return None


def short_id(value: str | None, n: int = 8) -> str:
    if not value:
        return "-"
    return value[:n]


def format_ids_box(session_id: str | None, trace_id: str | None) -> str:
    sid = session_id or "-"
    tid = trace_id or "-"
    return f"session_id: {sid}\ntrace_id: {tid}"


def session_label(session_id: str | None, n_turns: int | None = None, extra: str = "") -> str:
    sid = short_id(session_id, 12)
    if session_id:
        sid = f"`{sid}…`" if len(session_id) > 12 else f"`{session_id}`"
    else:
        sid = "_尚未开始_"
    turns = f" · 已记录 {n_turns} 轮" if n_turns is not None else ""
    suffix = f" · {extra}" if extra else ""
    return f"当前会话 {sid}{turns}{suffix}"


def format_upload_card(data: dict) -> str:
    doc_id = data.get("doc_id") or "-"
    status = data.get("status") or "-"
    chunks = data.get("chunks_count", "-")
    degraded = data.get("degraded")
    filename = data.get("filename") or "-"
    category = category_zh(data.get("category"))
    stage = data.get("ingest_stage")
    progress = data.get("ingest_progress")
    lines = [
        "### 入库结果",
        f"- **文件**: `{escape(filename)}`",
        f"- **doc_id**: `{doc_id}`",
        f"- **分类**: {category}",
        f"- **status**: `{status}`",
    ]
    if status == "processing":
        lines.append(
            "- 后台正在解析/索引，请刷新列表查看 **stage** 与 **ingest_progress**；"
            "完成前 **不可检索**，请勿重复上传同名文件。"
        )
    if stage:
        lines.append(f"- **ingest_stage**: `{stage}`")
    if progress is not None:
        lines.append(f"- **ingest_progress**: `{progress}`")
    if chunks != "-":
        lines.append(f"- **chunks_count**: `{chunks}`")
    if degraded is not None:
        flag = "是 ⚠️" if degraded else "否"
        lines.append(f"- **degraded**: {flag}")
    err = data.get("ingest_error")
    if err:
        lines.append(f"- **ingest_error**: {escape(str(err))}")
    return "\n".join(lines)


def format_doc_detail(data: dict) -> str:
    content = data.get("content") or ""
    preview = content[:1200]
    more = f"\n\n_（正文已截断，共 {len(content)} 字）_" if len(content) > 1200 else ""
    summary = data.get("summary") or "（无摘要）"
    lines = [
        f"### {data.get('title') or '未命名文档'}",
        f"- **doc_id**: `{data.get('id') or '-'}`",
        f"- **分类**: {category_zh(data.get('category'))}",
        f"- **status**: `{data.get('status') or '-'}` · **version**: `{data.get('version', '-')}`",
        f"- **chunks**: `{data.get('chunks_count', '-')}`",
        f"- **创建**: {data.get('create_time') or '-'} · **更新**: {data.get('update_time') or '-'}",
        f"- **摘要**: {summary}",
    ]
    stage = data.get("ingest_stage")
    if stage:
        lines.append(f"- **ingest_stage**: `{stage}`")
    progress = data.get("ingest_progress")
    if progress is not None:
        lines.append(f"- **ingest_progress**: `{progress}`")
    err = data.get("ingest_error")
    if err:
        lines.append(f"- **ingest_error**: ⚠️ {escape(str(err))}")
    assets = data.get("assets") or []
    if assets:
        lines.append("")
        lines.append(f"**内嵌资产**（{len(assets)} 项）")
        for a in assets[:20]:
            lines.append(
                f"- P{a.get('page', 0)} · {a.get('asset_type', '-')} · "
                f"`{a.get('storage_path', '-')}`"
            )
    lines.extend(["", "**正文预览**", "", preview + more])
    return "\n".join(lines)


def citation_lines(body: dict) -> list[str]:
    cites = body.get("citations") or []
    results = body.get("results") or []
    titles: dict[str, str] = {}
    cats: dict[str, str] = {}
    for item in results:
        did = item.get("doc_id") or ""
        if not did:
            continue
        titles.setdefault(did, item.get("title") or "")
        cats.setdefault(did, item.get("category") or "")
    lines: list[str] = []
    for did in cites[:8]:
        title = titles.get(did) or "未知文档"
        cat = category_zh(cats.get(did))
        lines.append(f"来源：《{title}》 ({short_id(did)}) · {cat}")
    return lines


def format_assistant_bubble(body: dict, with_answer: bool) -> str:
    """气泡只放回答 / 跳过原因 / 可读引用，不含召回全文。"""
    meta = body.get("llm_meta") or {}
    parts: list[str] = []
    if body.get("answer"):
        parts.append(body["answer"])
        cites = citation_lines(body)
        if cites:
            parts.append("**引用文档**")
            parts.extend(f"- {line}" for line in cites)
    elif with_answer:
        reason = meta.get("skip_reason") or "unknown"
        parts.append(f"⚠️ **LLM 未生成回答**：{human_skip(reason)}")
        if body.get("message"):
            parts.append(body["message"])
    else:
        parts.append("检索完成（未勾选 LLM 生成 / 未请求生成）。")
        if body.get("message"):
            parts.append(body["message"])
        if not (body.get("results") or []):
            parts.append(body.get("message") or "未检索到匹配内容。")
    return "\n\n".join(parts)


def _badge(label: str, on: bool, *, warn_when_true: bool = True) -> str:
    if on:
        cls = "badge badge-warn" if warn_when_true else "badge badge-ok"
        val = "是"
    else:
        cls = "badge badge-ok" if warn_when_true else "badge badge-muted"
        val = "否"
    return f'<span class="{cls}">{escape(label)} {val}</span>'


def _llm_badge(body: dict, with_answer: bool) -> str:
    meta = body.get("llm_meta") or {}
    skip = meta.get("skip_reason")
    if body.get("answer"):
        return '<span class="badge badge-ok">LLM 已生成</span>'
    if skip:
        return f'<span class="badge badge-warn">LLM 已跳过：{escape(human_skip(skip))}</span>'
    if with_answer or meta.get("requested"):
        return '<span class="badge badge-muted">LLM 未生成</span>'
    return '<span class="badge badge-muted">LLM 未请求</span>'


def _timing_rows(trace: dict | None) -> str:
    pairs = [
        ("embed", extract_step_ms_int(trace, "embed")),
        ("recall", extract_step_ms_int(trace, "recall")),
        ("rrf_fuse", extract_step_ms_int(trace, "rrf_fuse")),
        ("rerank", extract_step_ms_int(trace, "rerank")),
        ("llm", extract_step_ms_int(trace, "llm", "llm_answer")),
    ]
    known = [ms for _, ms in pairs if ms is not None]
    total = max(sum(known), 1)
    rows: list[str] = []
    for name, ms in pairs:
        if ms is None:
            rows.append(
                f'<div class="time-row"><span class="time-name">{name}</span>'
                f'<span class="time-track"><span class="time-bar" style="width:0%"></span></span>'
                f'<span class="time-ms">-</span></div>'
            )
            continue
        pct = max(4, min(100, int(round(100 * ms / total))))
        hot = " time-bar-hot" if name == "llm" and ms >= max(known or [0]) and ms > 200 else ""
        rows.append(
            f'<div class="time-row"><span class="time-name">{name}</span>'
            f'<span class="time-track"><span class="time-bar{hot}" style="width:{pct}%"></span></span>'
            f'<span class="time-ms">{ms} ms</span></div>'
        )
    return "".join(rows)


def format_status_html(body: dict, session_id: str | None, with_answer: bool) -> str:
    trace = body.get("trace") or {}
    meta = body.get("llm_meta") or {}
    tid = trace.get("trace_id") or body.get("trace_id") or "-"
    total = trace.get("total_ms", "-")
    badges = " ".join([
        _badge("降级", bool(body.get("degraded"))),
        _badge("兜底", bool(is_fallback(body))),
        _llm_badge(body, with_answer),
    ])
    return (
        f'<div class="status-panel">{badges}'
        f'<div class="meta-line">'
        f'<code>trace_id={escape(str(tid))}</code> · '
        f'<code>session={escape(short_id(session_id, 12))}</code> · '
        f'provider=<code>{escape(str(meta.get("provider") or "-"))}</code> · '
        f'called=<code>{escape(str(meta.get("called")))}</code> · '
        f'总耗时 <b>{escape(str(total))} ms</b>'
        f"</div>{_timing_rows(trace)}</div>"
    )


def format_query_gate_md(
    body: dict,
    user_query: str,
    api_query: str,
    spliced_n: int,
) -> str:
    meta = body.get("llm_meta") or {}
    skip = meta.get("skip_reason")
    rewritten = extract_rewritten_query(body)
    splice_note = (
        f"（会话历史 {spliced_n} 轮，未拼接进请求）" if spliced_n else "（未拼接历史）"
    )
    rewritten_line = rewritten if rewritten else "（响应未返回改写结果，不臆造）"
    return "\n".join([
        f"**用户原问题**: {user_query or '-'}",
        f"**实际请求 query**: {api_query or '-'} {splice_note}",
        f"**后端 query_original**: {body.get('query_original') or '-'}",
        f"**改写后 query**: {rewritten_line}",
        f"**message**: {body.get('message') or '-'}",
        f"**skip_reason**: {human_skip(skip)}",
        f"**llm.requested**: {meta.get('requested')} · **llm.enabled**: {meta.get('enabled')} · "
        f"**llm.called**: {meta.get('called')}",
    ])


def _score4(value) -> str:
    try:
        return f"{round(float(value or 0), 4)}"
    except (TypeError, ValueError):
        return "-"


def format_chunks_html(results: list[dict]) -> str:
    if not results:
        return "<p class='muted'>无召回片段（可能触发双闸门兜底）</p>"
    parts: list[str] = []
    seen_doc_assets: set[str] = set()
    for i, item in enumerate(results, 1):
        title = item.get("title") or "未命名文档"
        cat = category_zh(item.get("category"))
        source = item.get("source") or "-"
        chunk_n = item.get("chunk_index", "-")
        summary = item.get("summary")
        content = item.get("content") or ""
        preview = content[:PREVIEW_CHARS]
        extra = ""
        if len(content) > PREVIEW_CHARS:
            extra = (
                f"<details><summary>展开全文（{len(content)} 字）</summary>"
                f"<pre class='chunk-full'>{escape(content)}</pre></details>"
            )
        assets = item.get("assets") or []
        doc_id = item.get("doc_id") or ""
        if assets and doc_id and doc_id not in seen_doc_assets:
            seen_doc_assets.add(doc_id)
            n_img = sum(1 for a in assets if a.get("asset_type") == "image")
            n_tbl = sum(1 for a in assets if a.get("asset_type") == "table")
            pages = sorted({str(a.get("page", 0)) for a in assets})
            asset_note = (
                f"资产：图片 {n_img} · 表格 {n_tbl} · 页码 {', '.join(pages)}（见下方预览）"
            )
        elif assets:
            asset_note = "资产见上文同文档"
        else:
            asset_note = "无关联图片/表格"
        summary_html = (
            f"<div class='chunk-summary'>摘要：{escape(str(summary))}</div>"
            if summary else ""
        )
        parts.append(
            "<details class='chunk-card' open>"
            f"<summary><b>[{i}]</b> {escape(title)} · {escape(cat)} · "
            f"{escape(str(source))} · chunk#{escape(str(chunk_n))}</summary>"
            f"<div class='chunk-meta'>doc_id=<code>{escape(doc_id)}</code> · "
            f"RRF={_score4(item.get('score'))} · rerank={_score4(item.get('rerank_score'))}</div>"
            f"{summary_html}"
            f"<pre class='chunk-preview'>{escape(preview)}</pre>{extra}"
            f"<div class='chunk-assets'>{escape(asset_note)}</div>"
            "</details>"
        )
    return "<div class='chunk-list'>" + "".join(parts) + "</div>"


def chunks_table_rows(results: list[dict]) -> list[list]:
    rows: list[list] = []
    for item in results:
        rows.append([
            (item.get("content") or "")[:PREVIEW_CHARS],
            item.get("title") or "",
            item.get("doc_id") or "",
            category_zh(item.get("category")),
            item.get("chunk_index", ""),
            _score4(item.get("score")),
            _score4(item.get("rerank_score")),
            item.get("source") or "",
        ])
    return rows


def docs_table_rows(items: list[dict]) -> list[list]:
    rows: list[list] = []
    for doc in items:
        stage = doc.get("ingest_stage") or ""
        if doc.get("status") == "failed" and doc.get("ingest_error"):
            stage = f"{stage} ⚠️" if stage else "failed ⚠️"
        rows.append([
            doc.get("title") or "",
            category_zh(doc.get("category")),
            doc.get("status") or "",
            stage,
            doc.get("chunks_count", ""),
            (doc.get("create_time") or "")[:19],
            doc.get("id") or "",
        ])
    return rows


def html_table(rows: list[list] | None, caption: str = "", *, has_header: bool = True) -> str:
    if not rows:
        return ""
    if has_header:
        head, body = rows[0], rows[1:]
    else:
        width = max(len(r) for r in rows)
        head = [f"列{i+1}" for i in range(width)]
        body = rows
    thead = "<tr>" + "".join(f"<th>{escape(str(c))}</th>" for c in head) + "</tr>"
    tbody = "".join(
        "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
        for row in body[:80]
    )
    cap = f"<caption>{escape(caption)}</caption>" if caption else ""
    return f"<div class='asset-table'><table>{cap}{thead}{tbody}</table></div>"


def format_hit_tables_html(tables: list[dict]) -> str:
    if not tables:
        return "<p class='muted'>本轮无表格资产</p>"
    parts = [html_table(t.get("rows"), t.get("caption") or "") for t in tables]
    return "".join(p for p in parts if p) or "<p class='muted'>表格资产无法解析</p>"


def build_debug_dump(body: dict, session_id: str) -> str:
    trace = body.get("trace") or {}
    meta = body.get("llm_meta") or {}
    skip = meta.get("skip_reason")
    lines = [
        "=== 检索链路追踪（raw） ===",
        f"session_id: {session_id}",
        f"trace_id: {trace.get('trace_id') or body.get('trace_id') or '-'}",
        f"total_ms: {trace.get('total_ms', '-')}",
        f"degraded: {body.get('degraded')}",
        f"is_fallback: {is_fallback(body)}",
        f"skip_reason: {skip or '-'}",
        f"skip_reason_human: {human_skip(skip)}",
        f"llm.requested: {meta.get('requested')}",
        f"llm.enabled: {meta.get('enabled')}",
        f"llm.provider: {meta.get('provider')}",
        f"llm.called: {meta.get('called')}",
        f"query_original: {body.get('query_original') or '-'}",
        f"query_rewritten: {extract_rewritten_query(body) or '-'}",
        f"message: {body.get('message') or '-'}",
        "",
        "--- steps ---",
    ]
    for step in trace.get("steps") or []:
        lines.append(json.dumps(step, ensure_ascii=False))
    raw = json.dumps(body, ensure_ascii=False, default=str)
    if len(raw) > RAW_DUMP_MAX:
        raw = raw[:RAW_DUMP_MAX] + "\n... [truncated]"
    lines += ["", "--- raw data ---", raw]
    return "\n".join(lines)


def xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def pdf_iframe(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > PDF_PREVIEW_MAX:
        return (
            f"<p>PDF 超过 8MB，无法内嵌预览：<code>{escape(path.name)}</code>。"
            "请缩小文件或仅入库后在问答中检索。</p>"
        )
    b64 = base64.b64encode(data).decode("ascii")
    return (
        f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="420" '
        'style="border:1px solid #e8ddd3;background:#fff"></iframe>'
    )


def xlsx_preview(path: Path, max_rows: int = 40, max_cols: int = 12) -> list[list[str]] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root:
                    if xml_local(si.tag) != "si":
                        continue
                    texts = [
                        t.text or ""
                        for t in si.iter()
                        if xml_local(t.tag) == "t"
                    ]
                    strings.append("".join(texts))
            sheet = next(
                (n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                None,
            )
            if not sheet:
                return None
            root = ET.fromstring(zf.read(sheet))
            rows: list[list[str]] = []
            for row in root.iter():
                if xml_local(row.tag) != "row":
                    continue
                cells: list[str] = []
                for cell in row:
                    if xml_local(cell.tag) != "c":
                        continue
                    raw = ""
                    for child in cell:
                        if xml_local(child.tag) == "v":
                            raw = child.text or ""
                    if cell.get("t") == "s" and raw.isdigit():
                        idx = int(raw)
                        cells.append(strings[idx] if idx < len(strings) else raw)
                    else:
                        cells.append(raw)
                    if len(cells) >= max_cols:
                        break
                if any(cells):
                    rows.append(cells)
                if len(rows) >= max_rows:
                    break
            return rows or None
    except (OSError, KeyError, ET.ParseError, ValueError):
        return None


def docx_first_table(path: Path, max_rows: int = 30, max_cols: int = 12) -> list[list[str]] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
        for tbl in root.iter():
            if xml_local(tbl.tag) != "tbl":
                continue
            rows: list[list[str]] = []
            for tr in tbl:
                if xml_local(tr.tag) != "tr":
                    continue
                cells: list[str] = []
                for tc in tr:
                    if xml_local(tc.tag) != "tc":
                        continue
                    texts = [
                        t.text or ""
                        for t in tc.iter()
                        if xml_local(t.tag) == "t"
                    ]
                    cells.append("".join(texts))
                    if len(cells) >= max_cols:
                        break
                if cells:
                    rows.append(cells)
                if len(rows) >= max_rows:
                    break
            return rows or None
        return None
    except (OSError, KeyError, ET.ParseError):
        return None
