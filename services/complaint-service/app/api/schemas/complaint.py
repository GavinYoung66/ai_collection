"""投诉 API Request/Response Schemas（对齐 PRD 11.2）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.state import ReviewAction, TaskStatus, VerdictType


# ── 演示灌数（模拟中台推送）──

class DemoSeedItem(BaseModel):
    complaint_id: str
    scenario: str
    task_status: TaskStatus


class DemoSeedResponse(BaseModel):
    complaint_ids: list[str]
    items: list[DemoSeedItem]


# ── 创建工单 ──

class ComplaintCreateRequest(BaseModel):
    complaint_text: str = Field(..., min_length=1, description="投诉正文")
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    channel: str = Field(default="phone", description="投诉渠道")


class ComplaintCreateResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    created_at: datetime


# ── 列表 ──

class ComplaintListItem(BaseModel):
    complaint_id: str
    complaint_text: str
    task_status: TaskStatus
    verdict: Optional[VerdictType] = None
    created_at: datetime


class ComplaintListResponse(BaseModel):
    items: list[ComplaintListItem]
    total: int
    page: int
    page_size: int


# ── 详情 ──

class ComplaintDetailResponse(BaseModel):
    complaint_id: str
    complaint_text: str
    task_status: TaskStatus
    verdict: Optional[VerdictType] = None
    ai_verdict: Optional[VerdictType] = None
    disposition_verdict: Optional[VerdictType] = None
    disposition_reason: Optional[str] = None
    callback_scheduled: bool = False
    ordered_evidence: list[dict[str, Any]] = Field(default_factory=list)
    # COMP-03 stretch（非 A–G 必选）：RAG/citation doc_id，供 Demo/复核可见
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="证据引用 doc_id 列表（stretch COMP-03；≠ Batch-D）",
    )
    confidence: Optional[float] = None
    response_draft: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ── judge ──

class JudgeRequest(BaseModel):
    run_graph: bool = Field(default=False, description="是否真实跑图（默认 False stub）")


class JudgeResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    verdict: Optional[VerdictType] = None
    message: str = "judge accepted (stub)"


# ── status ──

class StatusResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    progress: int = 0


# ── verdict ──

class VerdictResponse(BaseModel):
    complaint_id: str
    verdict: Optional[VerdictType] = None
    confidence: Optional[float] = None
    response_draft: Optional[str] = None
    regulation_refs: list[str] = Field(default_factory=list)
    rag_source_ids: list[str] = Field(default_factory=list)
    # COMP-03 stretch：与 rag_source_ids/regulation_refs 对齐的可见 doc_id 列表
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="证据引用 doc_id（stretch COMP-03；非原 A–G 必选）",
    )


# ── agent logs ──

class AgentLogItem(BaseModel):
    node_name: str
    input_summary: str
    output_summary: str
    started_at: datetime
    duration_ms: int


class AgentLogsResponse(BaseModel):
    complaint_id: str
    logs: list[AgentLogItem]


# ── V3 trace ──

class TraceEventItem(BaseModel):
    category: str = Field(description="Agent / MCP / LLM / RAG / 其他")
    name: str
    started_at: datetime
    duration_ms: int = 0
    status: str = "success"
    summary: str = ""


class TraceResponse(BaseModel):
    complaint_id: str
    events: list[TraceEventItem] = Field(default_factory=list)
    langsmith_url: Optional[str] = None


# ── review ──

class ReviewRequest(BaseModel):
    review_action: ReviewAction
    reviewer: str = Field(default="operator", description="复核人角色")
    comment: Optional[str] = None
    reason: Optional[str] = Field(
        default=None,
        description="驳回等原因：reject_rejudge/modify_* 时必填（如 wrong_verdict）",
    )


class ReviewResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    review_action: ReviewAction


# ── V4 disposition / verdict ──

class DispositionVerdictRequest(BaseModel):
    establishment: VerdictType = Field(
        ...,
        description="成立性三选一（不可为 pending）",
    )
    reason: Optional[str] = Field(
        default=None,
        description="与 AI 初选不一致时必填改判原因",
    )
    reviewer: str = Field(default="operator")
    comment: Optional[str] = None


class DispositionVerdictResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    establishment: VerdictType
    ai_verdict: Optional[VerdictType] = None


class DispositionCallbackRequest(BaseModel):
    reviewer: str = Field(default="operator")
    note: Optional[str] = Field(default=None, description="回电/回访备注")
    comment: Optional[str] = None


class DispositionCallbackResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    callback_scheduled: bool = True
    establishment: Optional[VerdictType] = None


# ── close ──

class CloseRequest(BaseModel):
    closer: str = Field(default="operator", description="关闭人")
    reason: Optional[str] = None


class CloseResponse(BaseModel):
    complaint_id: str
    task_status: TaskStatus
    event_published: bool


# ── mode ──

class ModeSwitchRequest(BaseModel):
    mode: str = Field(..., description="single_agent | multi_agent")
    gray_percent: int = Field(default=100, ge=0, le=100)
    signers: list[str] = Field(
        ...,
        min_length=1,
        description="双签：须含 ops + tech",
    )


class ModeSwitchResponse(BaseModel):
    mode: str
    gray_percent: int


# ── stats ──

class StatsResponse(BaseModel):
    total: int
    by_verdict: dict[str, int]
    avg_processing_minutes: float
    evidence_insufficient_rate: float = 0.0
    mcp_failure_rate: float = 0.0
    ai_human_consistency_rate: float = 0.0


class ConsistencyResponse(BaseModel):
    ai_human_consistency_rate: float
    total_reviewed: int
    by_verdict_consistency: dict[str, float]
