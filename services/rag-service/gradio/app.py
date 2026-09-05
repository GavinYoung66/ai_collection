"""RAG 多模态知识库调试工作台（Gradio，内网）。左右 55:45，对话 + 链路追踪。"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import gradio as gr
import httpx

from formatters import (
    CATEGORY_SEARCH_CHOICES,
    CATEGORY_ZH_CHOICES,
    CHUNK_TABLE_HEADERS,
    DOC_TABLE_HEADERS,
    IMAGE_EXTS,
    NO_EMBED_PREVIEW_EXTS,
    STATUS_CHOICES,
    TEXT_EXTS,
    build_debug_dump,
    category_code,
    chunks_table_rows,
    docs_table_rows,
    docx_first_table,
    format_assistant_bubble,
    format_chunks_html,
    format_doc_detail,
    format_hit_tables_html,
    format_ids_box,
    format_query_gate_md,
    format_status_html,
    format_upload_card,
    html_table,
    pdf_iframe,
    session_label,
    xlsx_preview,
)

_RAG_API_BASE = os.environ.get("RAG_API_BASE", "http://localhost:8000/api/v1").rstrip("/")
_RAG_API_TOKEN = os.environ.get("RAG_API_TOKEN", "")
_KB_ASSETS_DIR = Path(os.environ.get("KB_ASSETS_DIR", "data/assets"))
_SUMMARY_MAX = int(os.environ.get("KB_SUMMARY_MAX_LEN", "200"))

_ALLOWED_EXTENSIONS = [
    "txt", "md", "pdf", "docx", "xlsx", "pptx",
    "png", "jpg", "jpeg", "mp3", "wav", "m4a", "amr", "wma",
]
_PAGE_SIZE = 20
_TIMEOUT = httpx.Timeout(90.0, connect=5.0)
# 入库含表格行/图片 OCR 逐条索引，常超过 90s；与检索超时分开，避免误报「后端不可用」
_UPLOAD_TIMEOUT = httpx.Timeout(600.0, connect=5.0)

_EXAMPLE_QUESTIONS = [
    ["和催收相关的法律法规有哪些？"],
    ["对债务人进行人工电话催收有哪些规范？"],
    ["逾期协商需要准备哪些材料？"],
    ["催收禁止使用哪些话术或行为？"],
    ["行业自律公约里对催收频次怎么规定？"],
]

_inflight: set[str] = set()

_CSS = """
.gradio-container { max-width: 100% !important; padding: 8px 12px !important; }
#workbench { align-items: stretch; }
.left-pane, .right-pane { min-height: 82vh; }
.right-pane {
  border-left: 1px solid #e8ddd3;
  padding-left: 12px !important;
  background: #faf8f6;
}
.badge {
  display: inline-block; padding: 2px 8px; margin: 0 6px 6px 0;
  border-radius: 999px; font-size: 12px; font-weight: 600;
}
.badge-ok { background: #e8f5e9; color: #2e7d32; }
.badge-warn { background: #fff3e0; color: #e65100; }
.badge-muted { background: #f0ece8; color: #6b5e54; }
.meta-line { margin: 8px 0 10px; font-size: 12px; color: #5c534c; }
.time-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; font-size: 12px; }
.time-name { width: 72px; color: #6b5e54; }
.time-track { flex: 1; background: #efe8e2; height: 8px; border-radius: 4px; overflow: hidden; }
.time-bar { display: block; height: 8px; background: #c4b5a5; }
.time-bar-hot { background: #e8590c; }
.time-ms { width: 84px; text-align: right; font-variant-numeric: tabular-nums; }
.chunk-card { margin: 0 0 8px; padding: 6px 8px; background: #fff; border: 1px solid #efe8e2; border-radius: 6px; }
.chunk-meta, .chunk-assets, .chunk-summary { font-size: 12px; color: #5c534c; margin: 4px 0; }
.chunk-preview, .chunk-full {
  white-space: pre-wrap; font-size: 12px; line-height: 1.45;
  background: #faf8f6; padding: 8px; border-radius: 4px; max-height: 280px; overflow: auto;
}
.asset-table { overflow: auto; margin: 8px 0 14px; }
.asset-table table { border-collapse: collapse; font-size: 12px; width: 100%; }
.asset-table th, .asset-table td { border: 1px solid #e8ddd3; padding: 4px 6px; }
.asset-table th { background: #f4eee8; }
.muted { color: #8a7d73; font-size: 13px; }
footer { display: none !important; }
"""

_THEME = gr.themes.Soft(
    primary_hue="orange",
    secondary_hue="stone",
    neutral_hue="stone",
)


def _headers(session_id: str | None = None) -> dict:
    headers: dict[str, str] = {}
    if _RAG_API_TOKEN:
        headers["Authorization"] = f"Bearer {_RAG_API_TOKEN}"
    if session_id:
        headers["X-Session-Id"] = str(session_id)[:64]
    return headers


def _check_token() -> None:
    if not _RAG_API_TOKEN:
        raise gr.Error("后端服务暂不可用：未配置访问凭证（RAG_API_TOKEN）")


def _parse_biz_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"后端返回异常（HTTP {resp.status_code}）"
    code = body.get("code", "")
    message = body.get("message", "")
    return f"后端返回错误：{message}（code={code}）"


def _file_path(file) -> Path | None:
    if file is None:
        return None
    if isinstance(file, dict):
        raw = file.get("path") or file.get("name")
        return Path(raw) if raw else None
    return Path(str(file))


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _acquire(op: str) -> None:
    if op in _inflight:
        raise gr.Error("正在处理上一请求，请稍候")
    _inflight.add(op)


def _try_acquire(op: str) -> bool:
    if op in _inflight:
        return False
    _inflight.add(op)
    return True


def _release(op: str) -> None:
    _inflight.discard(op)


def _health_urls() -> list[str]:
    urls = [f"{_RAG_API_BASE}/health"]
    if _RAG_API_BASE.endswith("/api/v1"):
        urls.append(f"{_RAG_API_BASE[: -len('/api/v1')]}/health")
    seen: list[str] = []
    for url in urls:
        if url not in seen:
            seen.append(url)
    return seen


def _escape_status(status: str) -> str:
    return status.replace("<", "").replace(">", "")[:32]


async def refresh_health() -> str:
    token_ok = bool(_RAG_API_TOKEN)
    token_html = (
        '<span class="badge badge-ok">Token 已配置</span>'
        if token_ok
        else '<span class="badge badge-warn">Token 未配置（RAG_API_TOKEN）</span>'
    )
    last_err = "不可达"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            for url in _health_urls():
                try:
                    resp = await client.get(url, headers=_headers())
                except httpx.HTTPError:
                    last_err = "连接失败"
                    continue
                if resp.status_code >= 400:
                    last_err = f"HTTP {resp.status_code}"
                    continue
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {}
                data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
                status = (data or {}).get("status") if isinstance(data, dict) else None
                status = status or "ok"
                cls = "badge-ok" if status in ("healthy", "ok", "degraded") else "badge-warn"
                if status == "unhealthy":
                    cls = "badge-warn"
                return (
                    f"<div>{token_html} "
                    f'<span class="badge {cls}">后端 {_escape_status(str(status))}</span> '
                    f'<span class="muted">{url}</span></div>'
                )
    except httpx.HTTPError:
        last_err = "连接失败"
    return (
        f'<div>{token_html} <span class="badge badge-warn">后端不可达（{last_err}）</span></div>'
    )


def preview_upload(file):
    empty = (
        gr.update(value=None, visible=False),
        gr.update(value="", visible=False),
        gr.update(value="", visible=False),
        "选择文件后可预览图片 / PDF / 文本 / 表格。pptx / 音频无内嵌预览，入库后可检索转写或文本。",
        "",
    )
    path = _file_path(file)
    if path is None or not path.exists():
        return empty
    ext = path.suffix.lower()
    hint = f"预览：`{path.name}`"
    if ext in IMAGE_EXTS:
        return (
            gr.update(value=str(path), visible=True),
            gr.update(value="", visible=False),
            gr.update(value="", visible=False),
            hint,
            "",
        )
    if ext == ".pdf":
        try:
            html = pdf_iframe(path)
        except OSError:
            html = f"<p>无法读取 PDF：{path.name}</p>"
        return (
            gr.update(value=None, visible=False),
            gr.update(value=html, visible=True),
            gr.update(value="", visible=False),
            hint,
            "",
        )
    if ext in TEXT_EXTS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            text = "(无法读取文本)"
        return (
            gr.update(value=None, visible=False),
            gr.update(value="", visible=False),
            gr.update(value=text, visible=True),
            hint,
            "",
        )
    if ext in NO_EMBED_PREVIEW_EXTS:
        return (
            gr.update(value=None, visible=False),
            gr.update(value="", visible=False),
            gr.update(value="", visible=False),
            f"{hint}（该格式无内嵌预览，入库后可在问答中检索转写/文本）",
            "",
        )
    table = None
    extra = "（该格式无内嵌预览，上传入库后可在问答命中时查看资产）"
    if ext == ".xlsx":
        table = xlsx_preview(path)
        extra = "（已解析表格预览）" if table else "（未能解析表格）"
    elif ext == ".docx":
        table = docx_first_table(path)
        extra = "（已解析文档内首张表格）" if table else extra
    table_html = html_table(table, path.name, has_header=False) if table else ""
    return (
        gr.update(value=None, visible=False),
        gr.update(value="", visible=False),
        gr.update(value="", visible=False),
        f"{hint}{extra}",
        table_html,
    )


def _load_table_preview(storage_path: str):
    try:
        path = _KB_ASSETS_DIR / storage_path
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        headers = data.get("headers") or []
        rows = data.get("rows") or []
        if not headers and not rows:
            return None
        return [list(headers)] + [list(r) for r in rows]
    except (OSError, ValueError, TypeError):
        return None


def _collect_hit_assets(results: list[dict]):
    images: list[tuple[str, str]] = []
    tables: list[dict] = []
    seen: set[str] = set()
    for item in results:
        title = item.get("title") or (item.get("doc_id") or "")[:8]
        for asset in item.get("assets") or []:
            storage_path = asset.get("storage_path", "")
            if not storage_path or storage_path in seen:
                continue
            seen.add(storage_path)
            page = asset.get("page", 0)
            atype = asset.get("asset_type") or ""
            caption = f"P{page} · {atype} · {title}"
            if atype == "image":
                abs_path = _KB_ASSETS_DIR / storage_path
                if abs_path.exists():
                    images.append((str(abs_path), caption))
            elif atype == "table":
                rows = _load_table_preview(storage_path)
                if rows:
                    tables.append({"caption": caption, "rows": rows})
    return images, tables


async def _list_docs(category_zh: str, status: str, page: int):
    page = max(int(page or 1), 1)
    params: dict = {"page": page, "page_size": _PAGE_SIZE}
    code = category_code(category_zh)
    if code:
        params["category"] = code
    if status and status != "全部":
        params["status"] = status
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_RAG_API_BASE}/kb/documents",
                params=params,
                headers=_headers(),
            )
    except httpx.HTTPError as e:
        raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
    if resp.status_code >= 400:
        raise gr.Error(_parse_biz_error(resp))
    payload = resp.json().get("data") or {}
    items = payload.get("items") or []
    total = int(payload.get("total") or 0)
    return docs_table_rows(items), total, page


async def _title_exists_with_status(filename: str, status: str) -> bool:
    """按 title + status 翻页穷尽检测（禁止只扫第一页）。"""
    page = 1
    page_size = 100
    max_pages = 1000
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            while page <= max_pages:
                resp = await client.get(
                    f"{_RAG_API_BASE}/kb/documents",
                    params={"status": status, "page": page, "page_size": page_size},
                    headers=_headers(),
                )
                if resp.status_code >= 400:
                    return False
                payload = resp.json().get("data") or {}
                items = payload.get("items") or []
                total = int(payload.get("total") or 0)
                if any((it.get("title") or "") == filename for it in items):
                    return True
                if not items or page * page_size >= total:
                    return False
                page += 1
    except httpx.HTTPError:
        return False
    return False


async def _find_processing_title(filename: str) -> bool:
    return await _title_exists_with_status(filename, "processing")


async def _find_active_title(filename: str) -> bool:
    return await _title_exists_with_status(filename, "active")


async def upload_document(file, category_zh: str, summary: str, list_cat: str, list_status: str):
    path = _file_path(file)
    inflight_key = f"upload:{path.name}" if path else "upload"
    if not _try_acquire(inflight_key):
        raise gr.Error(f"文件 `{path.name if path else ''}` 正在上传中，请稍候")
    try:
        _check_token()
        if path is None:
            raise gr.Error("请先选择要上传的文件")
        if path.suffix.lower().lstrip(".") not in _ALLOWED_EXTENSIONS:
            raise gr.Error(
                f"不支持的文件格式: {path.name}（支持: {'/'.join(_ALLOWED_EXTENSIONS)}）"
            )
        if await _find_active_title(path.name):
            raise gr.Error(
                f"同名文件 `{path.name}` 已在知识库中（active），请先删除后再上传"
            )
        if await _find_processing_title(path.name):
            raise gr.Error(
                f"同名文件 `{path.name}` 正在入库中（processing），请刷新列表查看进度，勿重复上传"
            )
        category = category_code(category_zh) or "regulation"
        summary = (summary or "").strip()
        if len(summary) > _SUMMARY_MAX:
            raise gr.Error(f"摘要超过上限 {_SUMMARY_MAX} 字符（KB_SUMMARY_MAX_LEN）")
        data_form: dict[str, str] = {"category": category}
        if summary:
            data_form["summary"] = summary
        try:
            async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                with open(path, "rb") as f:
                    resp = await client.post(
                        f"{_RAG_API_BASE}/kb/documents",
                        files={"file": (path.name, f, "application/octet-stream")},
                        data=data_form,
                        headers=_headers(),
                    )
        except httpx.ReadTimeout:
            card = (
                "### 入库处理中（未失败）\n"
                f"- **文件**: `{path.name}`\n"
                "- 后端仍在解析/索引（表格行、图片 OCR 会逐条写入，可能超过数分钟）。\n"
                "- 前端等待超时，**不是**后端宕机。请到下方列表刷新："
                "`processing` = 进行中，`active` = 完成。**请勿重复上传同一文件**。"
            )
            try:
                table, total, page = await _list_docs(
                    list_cat or "全部", list_status or "全部", 1
                )
                page_md = f"第 {page} 页 · 共 {total} 条"
            except gr.Error:
                table, page, page_md = [], 1, "请手动点「刷新列表」查看入库进度"
            return card, table, page, page_md
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
        except httpx.HTTPError as e:
            raise gr.Error(f"请求后端失败：{e.__class__.__name__}") from e
        if resp.status_code >= 400:
            raise gr.Error(_parse_biz_error(resp))
        data = resp.json().get("data") or {}
        data["filename"] = path.name
        data["category"] = category
        card = format_upload_card(data)
        try:
            table, total, page = await _list_docs(list_cat or "全部", list_status or "全部", 1)
            page_md = f"第 {page} 页 · 共 {total} 条"
        except gr.Error:
            table, page, page_md = [], 1, "入库已提交，但刷新列表失败"
        return card, table, page, page_md
    finally:
        _release(inflight_key)


async def refresh_docs(category_zh: str, status: str, page: int):
    if not _RAG_API_TOKEN:
        return [], max(int(page or 1), 1), "未配置访问凭证，列表不可用"
    try:
        rows, total, page = await _list_docs(category_zh, status, page)
        return rows, page, f"第 {page} 页 · 共 {total} 条"
    except gr.Error as e:
        return [], max(int(page or 1), 1), str(e)


async def docs_prev(category_zh: str, status: str, page: int):
    return await refresh_docs(category_zh, status, max(int(page or 1) - 1, 1))


async def docs_next(category_zh: str, status: str, page: int):
    return await refresh_docs(category_zh, status, int(page or 1) + 1)


def pick_doc(table, evt: gr.SelectData):
    row = getattr(evt, "row_value", None)
    if row:
        return str(list(row)[-1] or "")
    if evt is None or evt.index is None:
        return ""
    idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return ""
    if table is None:
        return ""
    if hasattr(table, "iloc"):
        try:
            row = list(table.iloc[idx])
        except Exception:
            return ""
    elif isinstance(table, list) and 0 <= idx < len(table):
        row = table[idx]
    else:
        return ""
    if not row:
        return ""
    return str(row[-1] or "")


async def view_doc(doc_id: str):
    _check_token()
    doc_id = (doc_id or "").strip()
    if not doc_id:
        raise gr.Error("请先在列表中点选一行")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_RAG_API_BASE}/kb/documents/{doc_id}",
                headers=_headers(),
            )
    except httpx.HTTPError as e:
        raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
    if resp.status_code >= 400:
        raise gr.Error(_parse_biz_error(resp))
    data = resp.json().get("data") or {}
    detail = format_doc_detail(data)
    assets = data.get("assets") or []
    images: list[str] = []
    for a in assets:
        if a.get("asset_type") == "image":
            sp = a.get("storage_path") or ""
            if sp:
                abs_path = _KB_ASSETS_DIR / sp
                if abs_path.exists():
                    images.append(str(abs_path))
    if images:
        gallery_md = "\n".join(f"![asset](file={p})" for p in images[:12])
        detail = detail + "\n\n**资产图片预览**\n\n" + gallery_md
    return detail


async def delete_doc(doc_id: str, confirm: bool, category_zh: str, status: str, page: int):
    _acquire("delete")
    try:
        _check_token()
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise gr.Error("请先在列表中点选一行")
        if not confirm:
            raise gr.Error("请先勾选「确认删除」")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.delete(
                    f"{_RAG_API_BASE}/kb/documents/{doc_id}",
                    headers=_headers(),
                )
        except httpx.HTTPError as e:
            raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
        if resp.status_code >= 400:
            raise gr.Error(_parse_biz_error(resp))
        rows, page, page_md = await refresh_docs(category_zh, status, page)
        return rows, page, page_md, False, f"已删除 `{doc_id}`", ""
    finally:
        _release("delete")


async def reindex_doc(doc_id: str, confirm: bool, category_zh: str, status: str, page: int):
    inflight_key = f"reindex:{(doc_id or '').strip()}"
    _acquire(inflight_key)
    try:
        _check_token()
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise gr.Error("请先在列表中点选一行")
        if not confirm:
            raise gr.Error(
                "请先勾选「确认重新索引」。注意：默认仅按 PG 正文重切，"
                "不会重建 docx/xlsx 内嵌图片与表格（assets_rebuilt=false）。"
            )
        try:
            async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
                resp = await client.post(
                    f"{_RAG_API_BASE}/kb/documents/{doc_id}/reindex",
                    headers=_headers(),
                )
        except httpx.ReadTimeout:
            rows, page, page_md = await refresh_docs(category_zh, status, page)
            msg = (
                f"重新索引 `{doc_id}` 仍在处理中（ReadTimeout）。"
                "请刷新列表查看 status，勿重复点击。"
            )
            return rows, page, page_md, False, msg
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
        except httpx.HTTPError as e:
            raise gr.Error(f"请求后端失败：{e.__class__.__name__}") from e
        if resp.status_code >= 400:
            raise gr.Error(_parse_biz_error(resp))
        data = resp.json().get("data") or {}
        rows, page, page_md = await refresh_docs(category_zh, status, page)
        chunks = data.get("chunks_count", "-")
        rebuilt = data.get("assets_rebuilt", False)
        return (
            rows, page, page_md, False,
            f"已重新索引 `{doc_id}` · chunks={chunks} · status={data.get('status', '-')} "
            f"· assets_rebuilt={rebuilt}（默认不重建多模态资产）",
        )
    finally:
        _release(inflight_key)


def _empty_qa(sid: str, extra: str):
    return (
        [],
        sid,
        [],
        "",
        session_label(sid, 0, extra),
        format_ids_box(sid, None),
        "_等待检索_",
        "_发送问题后展示本轮 query / 改写 / 跳过原因_",
        "<p class='muted'>等待检索</p>",
        [],
        None,
        "<p class='muted'>等待检索</p>",
        "",
    )


def new_session():
    return _empty_qa(_new_session_id(), "已换新 session")


def clear_session(session_id: str | None):
    return _empty_qa(session_id or _new_session_id(), "已清空对话（保留 session）")


async def ask_chat(
    query: str,
    top_k: int,
    category_zh: str | None,
    with_answer: bool,
    rewrite: bool,
    history: list | None,
    session_id: str | None,
    prev_queries: list | None,
):
    _acquire("search")
    try:
        _check_token()
        query = (query or "").strip()
        if not query:
            raise gr.Error("请输入问题")

        history = list(history or [])
        prev_queries = list(prev_queries or [])
        session_id = session_id or _new_session_id()

        spliced_n = len(prev_queries)
        api_query = query

        payload: dict = {
            "query": api_query,
            "top_k": int(top_k),
            "rewrite": bool(rewrite),
        }
        code = category_code(category_zh)
        if code:
            payload["category"] = code
        if with_answer:
            payload["answer"] = True

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_RAG_API_BASE}/kb/search",
                    json=payload,
                    headers=_headers(session_id),
                )
        except httpx.HTTPError as e:
            raise gr.Error(f"后端服务暂不可用：{e.__class__.__name__}") from e
        if resp.status_code >= 400:
            raise gr.Error(_parse_biz_error(resp))

        sid = resp.headers.get("X-Session-Id") or session_id
        body = resp.json().get("data") or {}
        results = body.get("results") or []

        history = history + [
            {"role": "user", "content": query},
            {"role": "assistant", "content": format_assistant_bubble(body, with_answer)},
        ]
        new_prev = (prev_queries + [query])[-10:]
        images, tables = _collect_hit_assets(results)
        gallery = [(p, c) for p, c in images] or None
        dump = build_debug_dump(body, sid)
        tid = (body.get("trace") or {}).get("trace_id") or body.get("trace_id")
        return (
            history,
            sid,
            new_prev,
            "",
            session_label(sid, len(new_prev)),
            format_ids_box(sid, tid),
            format_status_html(body, sid, with_answer),
            format_query_gate_md(body, query, api_query, spliced_n),
            format_chunks_html(results),
            chunks_table_rows(results),
            gallery,
            format_hit_tables_html(tables),
            dump,
        )
    finally:
        _release("search")


def build_ui() -> gr.Blocks:
    with gr.Blocks(
        title="RAG多模态知识库调试工作台",
        fill_height=True,
    ) as demo:
        session_state = gr.State(_new_session_id())
        prev_queries_state = gr.State([])
        docs_page_state = gr.State(1)

        gr.Markdown(
            "## RAG多模态知识库调试工作台（内网测试版，催收知识库专用）\n"
            "<span style='color:#6b5e54;font-size:13px'>"
            "左右 55:45 · 左栏问答/入库，右栏链路追踪 · 仅内部网络使用"
            "</span>"
        )
        with gr.Row():
            health_html = gr.HTML("<div class='muted'>正在探测后端…</div>")
            refresh_health_btn = gr.Button("刷新状态", size="sm")
        with gr.Row():
            session_md = gr.Markdown(session_label(None, extra="尚未开始"))
            ids_box = gr.Textbox(
                label="复制 session_id / trace_id",
                value=format_ids_box(None, None),
                lines=2,
                max_lines=2,
                interactive=False,
                buttons=["copy"],
            )

        with gr.Row(elem_id="workbench", equal_height=False):
            with gr.Column(scale=55, min_width=520, elem_classes=["left-pane"]):
                with gr.Tabs():
                    with gr.Tab("上传与知识库"):
                        file_input = gr.File(
                            label="选择文件（txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma）",
                            file_types=[f".{ext}" for ext in _ALLOWED_EXTENSIONS],
                        )
                        with gr.Row():
                            category_input = gr.Dropdown(
                                choices=CATEGORY_ZH_CHOICES,
                                value="制度法规",
                                label="分类",
                                scale=2,
                            )
                            summary_input = gr.Textbox(
                                label=f"可选摘要（最多 {_SUMMARY_MAX} 字，对应 form summary）",
                                placeholder="上游预生成摘要，可空",
                                lines=2,
                                max_lines=4,
                                scale=3,
                            )
                        upload_btn = gr.Button("上传入库", variant="primary")
                        upload_result = gr.Markdown("上传成功后在此展示 doc_id / status / chunks。")
                        gr.Markdown("**文件预览**（仅本 Tab，与问答命中预览隔离）")
                        upload_hint = gr.Markdown(
                            "选择文件后可预览图片 / PDF / 文本 / 表格。pptx / 音频无内嵌预览。"
                        )
                        upload_image = gr.Image(label="图片预览", visible=False, height=220)
                        upload_html = gr.HTML(visible=False)
                        upload_text = gr.Textbox(
                            label="文本预览", visible=False, lines=8, max_lines=16,
                            buttons=["copy"],
                        )
                        upload_table_html = gr.HTML("")

                        gr.Markdown("**知识库文档**")
                        with gr.Row():
                            list_cat = gr.Dropdown(
                                choices=CATEGORY_SEARCH_CHOICES, value="全部",
                                label="筛分类", scale=2,
                            )
                            list_status = gr.Dropdown(
                                choices=STATUS_CHOICES, value="全部",
                                label="筛状态", scale=2,
                            )
                            list_refresh_btn = gr.Button("刷新列表", size="sm", scale=1)
                        docs_table = gr.Dataframe(
                            headers=DOC_TABLE_HEADERS,
                            value=[],
                            interactive=False,
                            wrap=True,
                            max_height=280,
                            buttons=["copy"],
                            label="点击一行以选中 doc_id",
                        )
                        with gr.Row():
                            prev_page_btn = gr.Button("上一页", size="sm")
                            page_md = gr.Markdown("第 1 页")
                            next_page_btn = gr.Button("下一页", size="sm")
                        selected_doc = gr.Textbox(
                            label="选中 doc_id（可点选列表或粘贴）",
                            interactive=True,
                        )
                        with gr.Row():
                            view_btn = gr.Button("查看详情", size="sm")
                            confirm_del = gr.Checkbox(label="确认删除", value=False, scale=1)
                            del_btn = gr.Button("删除", size="sm")
                            confirm_reindex = gr.Checkbox(
                                label="确认重新索引（仅正文，不重建图片/表格）",
                                value=False,
                                scale=1,
                            )
                            reindex_btn = gr.Button("重新索引", size="sm")
                        doc_detail = gr.Markdown("_点选列表一行后可查看详情（含摘要）_")

                    with gr.Tab("问答"):
                        with gr.Row():
                            new_btn = gr.Button("新建会话", size="sm")
                            clear_btn = gr.Button("清空对话", size="sm")
                        gr.Markdown(
                            "<span style='color:#6b5e54;font-size:12px'>"
                            "新建会话 = 换新 session；清空对话 = 只清界面、保留 session"
                            "</span>"
                        )
                        chatbot = gr.Chatbot(
                            label="对话",
                            height="58vh",
                            layout="bubble",
                            buttons=["copy", "copy_all"],
                            placeholder="在下方输入问题。召回片段与资产预览在右侧，不在气泡内重复。",
                        )
                        with gr.Row():
                            query_input = gr.Textbox(
                                label="输入问题",
                                placeholder="Enter 发送。例如：和催收相关的法律法规有哪些？",
                                lines=2,
                                scale=5,
                            )
                            ask_btn = gr.Button("发送", variant="primary", scale=1)
                        with gr.Accordion("检索参数", open=False):
                            with gr.Row():
                                top_k_input = gr.Slider(
                                    minimum=1, maximum=20, value=5, step=1,
                                    label="召回数量 top_k", scale=2,
                                )
                                category_search = gr.Dropdown(
                                    choices=CATEGORY_SEARCH_CHOICES,
                                    value="全部",
                                    label="检索分类",
                                    scale=2,
                                )
                                with_answer_input = gr.Checkbox(
                                    label="生成 LLM 回答",
                                    value=True,
                                    scale=1,
                                )
                                rewrite_input = gr.Checkbox(
                                    label="Query 改写 rewrite",
                                    value=True,
                                    scale=1,
                                )
                        gr.Examples(
                            examples=_EXAMPLE_QUESTIONS,
                            inputs=[query_input],
                            label="催收域示例问题（点击填入）",
                        )

            with gr.Column(scale=45, min_width=400, elem_classes=["right-pane"]):
                gr.Markdown("### 检索链路追踪")
                with gr.Accordion("A. 状态与耗时", open=True):
                    status_html = gr.HTML("_等待检索_")
                with gr.Accordion("B. 本轮 Query 与门控", open=True):
                    query_gate_md = gr.Markdown("_发送问题后展示本轮 query / 改写 / 跳过原因_")
                with gr.Accordion("C. 召回片段", open=True):
                    with gr.Tabs():
                        with gr.Tab("卡片"):
                            chunks_html = gr.HTML("<p class='muted'>等待检索</p>")
                            hit_gallery = gr.Gallery(
                                label="命中图片（按页码 / 类型 / 文档）",
                                columns=3,
                                height=180,
                            )
                            hit_tables_html = gr.HTML("<p class='muted'>等待检索</p>")
                        with gr.Tab("表格视图"):
                            chunks_table = gr.Dataframe(
                                label="召回片段列表（chunk 级字段）",
                                headers=CHUNK_TABLE_HEADERS,
                                value=[],
                                interactive=False,
                                wrap=True,
                                max_height=360,
                                buttons=["copy"],
                            )
                with gr.Accordion("D. 原始 Trace", open=False):
                    debug_box = gr.Textbox(
                        label="steps + raw（可复制，超长已截断）",
                        lines=14,
                        max_lines=22,
                        buttons=["copy"],
                        interactive=False,
                    )

        demo.load(refresh_health, outputs=[health_html])
        refresh_health_btn.click(refresh_health, outputs=[health_html])

        file_input.change(
            preview_upload,
            inputs=[file_input],
            outputs=[upload_image, upload_html, upload_text, upload_hint, upload_table_html],
        )
        upload_btn.click(
            upload_document,
            inputs=[file_input, category_input, summary_input, list_cat, list_status],
            outputs=[upload_result, docs_table, docs_page_state, page_md],
            show_progress="full",
        )

        list_refresh_btn.click(
            refresh_docs,
            inputs=[list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md],
        )

        async def _reset_list(cat, st):
            return await refresh_docs(cat, st, 1)

        list_cat.change(
            _reset_list,
            inputs=[list_cat, list_status],
            outputs=[docs_table, docs_page_state, page_md],
        )
        list_status.change(
            _reset_list,
            inputs=[list_cat, list_status],
            outputs=[docs_table, docs_page_state, page_md],
        )
        prev_page_btn.click(
            docs_prev,
            inputs=[list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md],
        )
        next_page_btn.click(
            docs_next,
            inputs=[list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md],
        )
        docs_table.select(pick_doc, inputs=[docs_table], outputs=[selected_doc])
        view_btn.click(view_doc, inputs=[selected_doc], outputs=[doc_detail])
        del_btn.click(
            delete_doc,
            inputs=[selected_doc, confirm_del, list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md, confirm_del, doc_detail, selected_doc],
            show_progress="full",
        )
        reindex_btn.click(
            reindex_doc,
            inputs=[selected_doc, confirm_reindex, list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md, confirm_reindex, doc_detail],
            show_progress="full",
        )

        qa_outputs = [
            chatbot, session_state, prev_queries_state, query_input,
            session_md, ids_box, status_html, query_gate_md, chunks_html,
            chunks_table, hit_gallery, hit_tables_html, debug_box,
        ]
        ask_inputs = [
            query_input, top_k_input, category_search, with_answer_input, rewrite_input,
            chatbot, session_state, prev_queries_state,
        ]
        ask_btn.click(ask_chat, inputs=ask_inputs, outputs=qa_outputs, show_progress="full")
        query_input.submit(ask_chat, inputs=ask_inputs, outputs=qa_outputs, show_progress="full")
        new_btn.click(new_session, outputs=qa_outputs)
        clear_btn.click(clear_session, inputs=[session_state], outputs=qa_outputs)

        demo.load(
            refresh_docs,
            inputs=[list_cat, list_status, docs_page_state],
            outputs=[docs_table, docs_page_state, page_md],
        )

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        css=_CSS,
        theme=_THEME,
    )
