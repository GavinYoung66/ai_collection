"""LangGraph State Schema——投诉研判全量状态与 Pydantic 模型。

对齐 PRD 6.1 State Schema 与 complaint_session ORM 字段。
FATAL-3: case_evidence 使用 Annotated[dict[str, CaseEvidenceBundle], merge_case_evidence]
        自定义 reducer 消除并行证据节点写入竞态。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional

import structlog
from pydantic import AliasChoices, BaseModel, Field, model_validator

from app.service.evidence_refs import merge_unique_doc_ids

logger = structlog.get_logger()


# ── 枚举 ──────────────────────────────────────────────

class TaskStatus(str, Enum):
    """任务状态（PRD 6.1 + V4 pending_callback）。"""

    pending = "pending"
    evidence_gathering = "evidence_gathering"
    reflection_retry = "reflection_retry"
    judging = "judging"
    draft_generated = "draft_generated"
    pending_review = "pending_review"
    pending_senior_review = "pending_senior_review"
    pending_callback = "pending_callback"  # V4：定责完成，待回电处置
    resolved = "resolved"
    closed = "closed"
    archived = "archived"


class VerdictType(str, Enum):
    """判责类型（4 值，不含已废弃的 normal）。"""

    agent_violation = "agent_violation"
    user_misunderstanding = "user_misunderstanding"
    compliant_but_poor_experience = "compliant_but_poor_experience"
    pending = "pending"


class ConfidenceLevel(str, Enum):
    """置信度等级（3 值）。"""

    high = "high"
    medium = "medium"
    low = "low"


class ComplianceVerdict(str, Enum):
    """整体合规判定（4 值 = LLM prompt 值域 + 节点降级值域并集）。

    prompt enum_constraints 限定 compliant/partial_violation/violation；
    降级路径（degrade_llm）额外写入 uncertain，故并入以避免收紧后降级被打回。
    """

    compliant = "compliant"
    partial_violation = "partial_violation"
    violation = "violation"
    uncertain = "uncertain"


class EmotionLevel(str, Enum):
    """用户情绪等级（5 值：prompt 4 值 + 降级 unknown）。"""

    calm = "calm"
    annoyed = "annoyed"
    angry = "angry"
    extreme = "extreme"
    unknown = "unknown"


class ComplaintRiskLevel(str, Enum):
    """二次投诉风险等级（4 值：prompt 3 值 + 降级 unknown）。"""

    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


class ReviewAction(str, Enum):
    """人工复核动作（6 值）。"""

    approve = "approve"
    modify_verdict = "modify_verdict"
    modify_response = "modify_response"
    reject_rejudge = "reject_rejudge"
    add_evidence = "add_evidence"
    escalate = "escalate"


# ── 证据模型 ──────────────────────────────────────────

class EvidenceItem(BaseModel):
    """单条证据条目（对齐批次2 evidence_helper / PRD：confidence 数值 + confidence_level 等级）。"""

    evidence_id: str = Field(default="", description="证据唯一标识")
    evidence_type: str = Field(default="", description="证据类型：call_asr/qc_alert/case_info/history_ticket/history_complaint")
    content: str = Field(default="", description="证据内容文本")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="数值置信度 0.0~1.0")
    confidence_level: ConfidenceLevel = Field(
        default=ConfidenceLevel.high, description="置信度等级 high/medium/low"
    )
    is_corrupted: bool = Field(default=False, description="是否乱码/损坏")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")

    model_config = {"extra": "allow"}


class CaseEvidenceBundle(BaseModel):
    """单案件证据包。"""

    case_id: str = Field(default="", description="案件 ID")
    case_info: dict[str, Any] = Field(default_factory=dict, description="案件基础信息")
    history_tickets: list[dict[str, Any]] = Field(default_factory=list, description="历史工单列表")
    history_complaints: list[dict[str, Any]] = Field(default_factory=list, description="历史投诉列表")
    call_evidence: list[dict[str, Any]] = Field(default_factory=list, description="通话 ASR 证据")
    qc_alerts: list[dict[str, Any]] = Field(default_factory=list, description="质检告警证据")


# ── Agent 输出模型 ────────────────────────────────────

class ReflectionReport(BaseModel):
    """反思报告。"""

    evidence_sufficient: bool = Field(..., description="证据是否充分")
    missing_evidence: list[str] = Field(default_factory=list, description="缺失证据清单")
    contradictions: list[str] = Field(default_factory=list, description="核心冲突点")
    per_case_reflection: dict[str, Any] = Field(default_factory=dict, description="逐案反思")
    suggestion: str = Field(default="", description="反思建议")

    model_config = {"extra": "ignore"}


class TimelineEvent(BaseModel):
    """时序单条事件（字段对齐 fact_timeline fixture 实际形态）。

    extra=allow：容忍 LLM/上游追加字段，但已知字段的类型与必填受约束。
    """

    event_time: str = Field(default="", description="事件时间")
    source: str = Field(default="", description="来源：complaint/call/...")
    content: str = Field(default="", description="事件内容")

    model_config = {"extra": "allow"}


class FactTimeline(BaseModel):
    """事实时序。"""

    timeline: list[TimelineEvent] = Field(default_factory=list, description="时序事件列表")
    summary: str = Field(default="", description="事实摘要")


class ComplianceItem(BaseModel):
    """单条行为合规意见（字段对齐 compliance fixture 实际形态）。

    extra=allow：容忍 LLM 追加字段，但 behavior/is_violation 的类型受约束。
    """

    behavior: str = Field(default="", description="被评估的行为")
    is_violation: bool = Field(default=False, description="是否违规")
    regulation_refs: list[str] = Field(default_factory=list, description="引用法规 ID")
    reasoning: str = Field(default="", description="判定理由")

    model_config = {"extra": "allow"}


class ComplianceOpinion(BaseModel):
    """合规意见。"""

    overall_compliance: ComplianceVerdict = Field(
        ..., description="整体合规判定：compliant/partial_violation/violation/uncertain"
    )
    regulation_refs: list[str] = Field(default_factory=list, description="引用法规来源 ID")
    opinions: list[ComplianceItem] = Field(default_factory=list, description="逐条行为合规意见")
    details: str = Field(default="", description="合规分析详情")


class UserDemandAnalysis(BaseModel):
    """用户诉求分析。"""

    # demand_type 保持 str：降级路径写入 "[规则兜底] {rule_value}" 自由文本，无法枚举化；
    # LLM 路径由 prompt enum_constraints 限定 refund/apology/correction/clarification/other
    demand_type: str = Field(..., description="诉求类型（LLM：refund/apology/correction/clarification/other）")
    emotion_level: EmotionLevel = Field(..., description="情绪等级：calm/annoyed/angry/extreme")
    secondary_complaint_risk: ComplaintRiskLevel = Field(
        ..., description="二次投诉风险：low/medium/high"
    )


class VerdictDraft(BaseModel):
    """判责草稿（批次6 节点使用，本批定义供复用）。"""

    verdict: VerdictType = Field(..., description="判责结果（四值，不含 normal）")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0.0~1.0")
    reasoning: str = Field(default="", description="判责理由")
    conflicts: list[str] = Field(default_factory=list, description="冲突点")
    conflict_highlight: bool = Field(default=False, description="是否标记冲突需人工复核")
    per_case_verdicts: dict[str, VerdictType] = Field(
        default_factory=dict, description="逐案判责 {case_id: VerdictType}"
    )

    @model_validator(mode="after")
    def _normalize_conflict_flag(self) -> VerdictDraft:
        """软校验跨字段一致性：标记冲突但冲突列表为空 → 自动降级并告警。

        只做归一不抛异常——该字段仅是人工复核提示，不应因冗余标记打断研判链路。
        """
        if self.conflict_highlight and not self.conflicts:
            logger.warning("conflict_flag_without_conflicts", verdict=str(self.verdict))
            self.conflict_highlight = False
        return self


class JudgmentPackage(BaseModel):
    """研判数据包（批次6 节点使用）。"""

    evidence_chain: list[dict[str, Any]] = Field(default_factory=list, description="证据链")
    judgment_report: str = Field(
        default="",
        description="研判报告",
        validation_alias=AliasChoices("judgment_report", "verdict_report"),
    )
    response_draft: str = Field(default="", description="答辩初稿")
    internal_improvement: str = Field(default="", description="内部改进建议")
    rag_source_ids: list[str] = Field(default_factory=list, description="RAG 来源 ID")
    regulation_refs: list[str] = Field(default_factory=list, description="法规引用（兼容旧 fixture）")

    model_config = {"extra": "ignore", "populate_by_name": True}


# ── 异常 / 人工 ───────────────────────────────────────

class ExceptionInfo(BaseModel):
    """异常信息。"""

    error_type: str = Field(default="", description="异常类型")
    message: str = Field(default="", description="异常消息")
    node: str = Field(default="", description="发生节点")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat(), description="时间戳")

    model_config = {"extra": "ignore"}


class HumanReviewResult(BaseModel):
    """人工复核结果（批次6 节点使用）。"""

    review_action: Optional[ReviewAction] = Field(default=None, description="复核动作")
    reviewer: str = Field(default="", description="复核人")
    comment: str = Field(default="", description="复核意见")
    reject_count: int = Field(default=0, description="驳回重判次数")


# ── 全局 State ────────────────────────────────────────

def merge_case_evidence(
    left: dict[str, CaseEvidenceBundle],
    right: dict[str, CaseEvidenceBundle],
) -> dict[str, CaseEvidenceBundle]:
    """case_evidence 自定义 reducer（FATAL-3 / PRD 6.1 字段级覆盖）。

    按 case_id 合并：
    - 新 case_id 直接加入
    - 同 case_id：右侧字段「有值」则整字段替换左侧（非列表拼去重）
    保证 evidence_case 与 evidence_call_qc 并行写入互补字段时无竞态丢失。
    """
    merged: dict[str, CaseEvidenceBundle] = {}
    for cid, b in left.items():
        merged[cid] = b if isinstance(b, CaseEvidenceBundle) else CaseEvidenceBundle(**b)
    for cid, right_bundle in right.items():
        rb = right_bundle if isinstance(right_bundle, CaseEvidenceBundle) else CaseEvidenceBundle(**right_bundle)
        if cid not in merged:
            merged[cid] = rb
            continue
        merged[cid] = _merge_bundle(merged[cid], rb)
    return merged


def _merge_bundle(left: CaseEvidenceBundle, right: CaseEvidenceBundle) -> CaseEvidenceBundle:
    """合并同一 case_id 的两个 CaseEvidenceBundle（PRD 字段级覆盖）。

    右侧字段有值（非空）则整字段替换左侧；右侧为空则保留左侧。
    """
    def _pick(l, r):
        return r if r else l

    return CaseEvidenceBundle(
        case_id=left.case_id,
        case_info=_pick(left.case_info, right.case_info),
        history_tickets=_pick(left.history_tickets, right.history_tickets),
        history_complaints=_pick(left.history_complaints, right.history_complaints),
        call_evidence=_pick(left.call_evidence, right.call_evidence),
        qc_alerts=_pick(left.qc_alerts, right.qc_alerts),
    )


class ComplaintSessionState(BaseModel):
    """LangGraph 全局状态（对齐 complaint_session ORM 字段）。"""

    session_id: str = Field(..., description="会话 ID")
    trace_id: str = Field(default="", description="链路追踪 ID")
    complaint_id: Optional[str] = Field(default=None, description="投诉 ID")
    case_ids: list[str] = Field(default_factory=list, description="关联案件 ID 列表")
    session_ids: list[str] = Field(default_factory=list, description="关联会话 ID 列表")
    user_id: Optional[str] = Field(default=None, description="用户 ID")
    complaint_text: str = Field(default="", description="投诉文本")

    # 反思循环控制
    loop_count: int = Field(default=0, description="反思循环次数")
    max_loops: int = Field(default=3, description="最大循环次数")
    reject_count: int = Field(default=0, description="驳回重判次数（权威计数）")

    # 证据数据
    case_evidence: Annotated[dict[str, CaseEvidenceBundle], merge_case_evidence] = Field(
        default_factory=dict, description="案件证据包 {case_id: CaseEvidenceBundle}"
    )
    cross_case_evidence: list[dict[str, Any]] = Field(default_factory=list, description="跨案件证据")
    historical_complaint_evidence: list[dict[str, Any]] = Field(default_factory=list, description="历史投诉证据")

    # Agent 中间输出
    reflection_report: Optional[ReflectionReport] = Field(default=None, description="反思报告")
    fact_timeline: Optional[FactTimeline] = Field(default=None, description="事实时序")
    compliance_opinion: Optional[ComplianceOpinion] = Field(default=None, description="合规意见")
    user_demand: Optional[UserDemandAnalysis] = Field(default=None, description="用户诉求分析")
    verdict_draft: Optional[VerdictDraft] = Field(default=None, description="判责草稿")
    judgment_package: Optional[JudgmentPackage] = Field(default=None, description="研判数据包")
    # MCP rag_kb 真命中 doc_id（供 L4 allowlist / evidence_refs；节点侧 union merge）
    rag_hit_doc_ids: Annotated[list[str], merge_unique_doc_ids] = Field(
        default_factory=list,
        description="MCP 检索命中的 doc_id 白名单（合规/模板等节点累计）",
    )

    # 异常与人工
    exception_info: Optional[ExceptionInfo] = Field(default=None, description="异常信息")
    human_review: Optional[HumanReviewResult] = Field(default=None, description="人工复核结果")

    # 任务状态与 Token
    task_status: TaskStatus = Field(default=TaskStatus.pending, description="任务状态")
    total_tokens_consumed: int = Field(default=0, description="累计 Token 消耗")
    max_token_threshold: int = Field(default=50000, description="Token 阈值")
    mode: str = Field(default="multi_agent", description="运行模式")
