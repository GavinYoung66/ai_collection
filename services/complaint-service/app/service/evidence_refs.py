"""COMP-03：evidence_refs / doc_id 可见性（非原 A–G 必选；≠ Batch-D）。"""
from __future__ import annotations

from typing import Any, Iterable


def build_evidence_refs(
    *id_lists: Iterable[Any],
) -> list[str]:
    """合并 rag_source_ids / regulation_refs 等为去重后的 doc_id 列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for ids in id_lists:
        for raw in ids or []:
            doc_id = str(raw or "").strip()
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            out.append(doc_id)
    return out


def merge_unique_doc_ids(left: list[str], right: list[str]) -> list[str]:
    """LangGraph reducer：合并两侧 MCP 命中 doc_id。"""
    return build_evidence_refs(left or [], right or [])


def _as_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:
            return {}
    return {}


def extract_doc_ids_from_hits(rag_result: list | Any) -> list[str]:
    """从 MCP rag_kb 命中列表提取 doc_id。"""
    if not isinstance(rag_result, list):
        rag_result = [rag_result] if rag_result else []
    ids: list[str] = []
    for item in rag_result:
        if isinstance(item, dict):
            doc_id = item.get("doc_id") or item.get("id") or item.get("template_id")
            if doc_id:
                ids.append(str(doc_id))
        elif isinstance(item, str) and item.strip():
            ids.append(item.strip())
    return build_evidence_refs(ids)


def extract_citations_from_graph_final(
    final: dict[str, Any] | None,
) -> tuple[list[str], list[str], list[str]]:
    """从 graph 终态提取 (allowed_rag_doc_ids, regulation_refs, rag_source_ids)。

    ``allowed`` **仅**来自节点写入的 ``rag_hit_doc_ids``（MCP 真命中），
    禁止用 package / opinion 自报引用扩充白名单（否则 L4 可被自证绕过）。
    regs/srcs 仍从 package/opinion 提取，由 GET /verdict 的 L4 按 allowed 过滤。
    """
    final = final if isinstance(final, dict) else {}
    mcp_hits = list(final.get("rag_hit_doc_ids") or [])

    pkg = _as_mapping(final.get("judgment_package"))
    srcs = list(pkg.get("rag_source_ids") or [])
    regs = list(pkg.get("regulation_refs") or [])

    opinion = _as_mapping(final.get("compliance_opinion"))
    regs.extend(opinion.get("regulation_refs") or [])
    for op in opinion.get("opinions") or []:
        if isinstance(op, dict):
            regs.extend(op.get("regulation_refs") or [])

    # 兼容误写在顶层的字段（仍不进入 allowed）
    regs.extend(final.get("regulation_refs") or [])
    srcs.extend(final.get("rag_source_ids") or [])

    regs = build_evidence_refs(regs)
    srcs = build_evidence_refs(srcs)
    allowed = build_evidence_refs(mcp_hits)
    return allowed, regs, srcs
