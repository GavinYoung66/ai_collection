"""投诉工单路由（8 接口 + close）。

对齐 PRD 11.2。judge 默认 stub，不跑全图。
注意：此 router 含 /{id}，必须在 stats/mode 之后挂载（防吞路径）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Query, Request

from app.api.response import fail, success
from app.api.schemas.complaint import (
    AgentLogsResponse,
    CloseRequest,
    CloseResponse,
    ComplaintCreateRequest,
    ComplaintCreateResponse,
    ComplaintDetailResponse,
    ComplaintListItem,
    ComplaintListResponse,
    DemoSeedItem,
    DemoSeedResponse,
    DispositionVerdictRequest,
    DispositionVerdictResponse,
    DispositionCallbackRequest,
    DispositionCallbackResponse,
    JudgeRequest,
    JudgeResponse,
    ReviewRequest,
    ReviewResponse,
    StatusResponse,
    TraceEventItem,
    TraceResponse,
    VerdictResponse,
)
from app.service.complaint_service import ComplaintNotFound, complaint_service
from app.service.guardrails_postcheck import postcheck
from common.errors.codes import ErrorCode

router = APIRouter(prefix="/complaints", tags=["complaints"])


@router.post("")
async def create_complaint(req: ComplaintCreateRequest, request: Request):
    rec = complaint_service.create(
        complaint_text=req.complaint_text,
        session_id=req.session_id,
        case_id=req.case_id,
        channel=req.channel,
    )
    data = ComplaintCreateResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        created_at=rec["created_at"],
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("")
async def list_complaints(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = complaint_service.list(page=page, page_size=page_size)
    items = [
        ComplaintListItem(
            complaint_id=r["complaint_id"],
            complaint_text=r["complaint_text"],
            task_status=r["task_status"],
            verdict=r["verdict"],
            created_at=r["created_at"],
        ).model_dump(mode="json")
        for r in result["items"]
    ]
    data = ComplaintListResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.post("/demo/seed")
async def seed_demo_complaints(request: Request):
    """模拟数据中台推送：一次创建 3 条场景工单（含 pending 样例）。须挂在 /{id} 之前。"""
    records = complaint_service.seed_demo()
    data = DemoSeedResponse(
        complaint_ids=[r["complaint_id"] for r in records],
        items=[DemoSeedItem(**r) for r in records],
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/events")
async def list_published_events(
    request: Request,
    topic: Optional[str] = Query(default=None),
):
    """演示用：进程内 publisher 事件列表（mock 下游可观测）。须挂在 /{id} 之前。"""
    from app.events.publisher import publisher as pub

    evs = pub.list_events(topic)
    items = [{"topic": e.topic, "payload": e.payload} for e in evs]
    return success(data={"items": items, "total": len(items)}, request=request)


@router.get("/{complaint_id}")
async def get_complaint(complaint_id: str, request: Request):
    try:
        rec = complaint_service.get_or_raise(complaint_id)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    from app.models.state import VerdictType
    from app.service.evidence_refs import build_evidence_refs
    from app.service.ordered_evidence import build_ordered_evidence

    disp = rec.get("disposition_verdict")
    disp_val = disp.value if hasattr(disp, "value") else disp
    ordered: list = []
    if disp_val == VerdictType.agent_violation.value:
        ordered = build_ordered_evidence(rec)
    evidence_refs = build_evidence_refs(
        rec.get("rag_source_ids"),
        rec.get("regulation_refs"),
    )
    data = ComplaintDetailResponse(
        complaint_id=rec["complaint_id"],
        complaint_text=rec["complaint_text"],
        task_status=rec["task_status"],
        verdict=rec["verdict"],
        ai_verdict=rec.get("ai_verdict"),
        disposition_verdict=rec.get("disposition_verdict"),
        disposition_reason=rec.get("disposition_reason"),
        callback_scheduled=bool(rec.get("callback_scheduled")),
        ordered_evidence=ordered,
        evidence_refs=evidence_refs,
        confidence=rec["confidence"],
        response_draft=rec["response_draft"],
        created_at=rec["created_at"],
        updated_at=rec["updated_at"],
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.post("/{complaint_id}/judge")
async def judge_complaint(complaint_id: str, request: Request, req: JudgeRequest = Body(default=None)):
    run_graph = bool(req.run_graph) if req is not None else False
    try:
        rec = await complaint_service.judge_async(complaint_id, run_graph=run_graph)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    data = JudgeResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        verdict=rec["verdict"],
        message="judge accepted (graph)" if run_graph else "judge accepted (stub)",
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/{complaint_id}/status")
async def get_status(complaint_id: str, request: Request):
    try:
        rec = complaint_service.get_status(complaint_id)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    data = StatusResponse(**rec)
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/{complaint_id}/verdict")
async def get_verdict(complaint_id: str, request: Request):
    try:
        rec = complaint_service.get_verdict(complaint_id)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    # L4：合法 RAG doc_id 必须独立提供，禁止用 package 自身 source 自证
    allowed = list(rec.pop("allowed_rag_doc_ids", None) or [])
    stop_call_flag = rec.pop("stop_call_flag", False)
    ok, sanitized = postcheck(rec, rag_doc_ids=allowed)
    sanitized.setdefault("complaint_id", complaint_id)
    # 写回 stub 上的停止致电标记（红线3）
    if ok and sanitized.get("stop_call_flag"):
        store = complaint_service.get(complaint_id)
        if store is not None:
            store["stop_call_flag"] = True
    elif not ok:
        stop_call_flag = False
    from app.service.evidence_refs import build_evidence_refs

    regs = list(sanitized.get("regulation_refs") or [])
    srcs = list(sanitized.get("rag_source_ids") or [])
    data = VerdictResponse(
        complaint_id=sanitized.get("complaint_id", complaint_id),
        verdict=sanitized.get("verdict"),
        confidence=sanitized.get("confidence"),
        response_draft=sanitized.get("response_draft"),
        regulation_refs=regs,
        rag_source_ids=srcs,
        evidence_refs=build_evidence_refs(srcs, regs),
    )
    _ = stop_call_flag  # 字段已在 store；Response schema 本批可不暴露
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/{complaint_id}/agent-logs")
async def get_agent_logs(complaint_id: str, request: Request):
    try:
        rec = complaint_service.get_agent_logs(complaint_id)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    data = AgentLogsResponse(complaint_id=rec["complaint_id"], logs=rec["logs"])
    return success(data=data.model_dump(mode="json"), request=request)


@router.get("/{complaint_id}/trace")
async def get_complaint_trace(complaint_id: str, request: Request):
    """V3 研判链路时间线（落盘 events；可选 langsmith_url）。"""
    try:
        rec = complaint_service.get_trace(complaint_id)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    events = [TraceEventItem(**e) for e in rec["events"]]
    data = TraceResponse(
        complaint_id=rec["complaint_id"],
        events=events,
        langsmith_url=rec.get("langsmith_url"),
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.put("/{complaint_id}/disposition/verdict")
async def disposition_verdict(
    complaint_id: str, req: DispositionVerdictRequest, request: Request
):
    """V4 定责：成立性三选一 → pending_callback。"""
    try:
        rec = complaint_service.submit_disposition_verdict(
            complaint_id,
            establishment=req.establishment,
            reviewer=req.reviewer,
            reason=req.reason,
            comment=req.comment,
        )
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    except ValueError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    except RuntimeError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    data = DispositionVerdictResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        establishment=req.establishment,
        ai_verdict=rec.get("ai_verdict"),
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.put("/{complaint_id}/disposition/callback")
async def disposition_callback(
    complaint_id: str, request: Request, req: DispositionCallbackRequest = Body(default=None)
):
    """V4 回电处置：发布 callback.requested → resolved（幂等）。"""
    body = req if req is not None else DispositionCallbackRequest()
    try:
        rec = complaint_service.submit_disposition_callback(
            complaint_id,
            reviewer=body.reviewer,
            note=body.note,
            comment=body.comment,
        )
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    except ValueError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    except RuntimeError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    est = rec.get("disposition_verdict") or rec.get("verdict")
    data = DispositionCallbackResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        callback_scheduled=bool(rec.get("callback_scheduled")),
        establishment=est,
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.put("/{complaint_id}/review")
async def review_complaint(complaint_id: str, req: ReviewRequest, request: Request):
    try:
        rec = complaint_service.review(
            complaint_id, review_action=req.review_action,
            reviewer=req.reviewer, comment=req.comment, reason=req.reason,
        )
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    except ValueError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    data = ReviewResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        review_action=req.review_action,
    )
    return success(data=data.model_dump(mode="json"), request=request)


@router.put("/{complaint_id}/close")
async def close_complaint(complaint_id: str, request: Request, req: CloseRequest = Body(default=None)):
    closer = req.closer if req is not None else "operator"
    reason = req.reason if req is not None else None
    try:
        rec = complaint_service.close(complaint_id, closer=closer, reason=reason)
    except ComplaintNotFound:
        return fail(ErrorCode.A5001, f"投诉工单不存在: {complaint_id}", request=request)
    except ValueError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    except RuntimeError as e:
        return fail(ErrorCode.A5002, str(e), request=request)
    data = CloseResponse(
        complaint_id=rec["complaint_id"],
        task_status=rec["task_status"],
        event_published=True,
    )
    return success(data=data.model_dump(mode="json"), request=request)
