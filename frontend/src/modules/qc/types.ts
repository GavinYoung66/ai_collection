/**
 * 合规质检模块类型契约（唯一来源：qc-service 后端源码，design D6）。
 *
 * 核实记录（2026-10-23 逐字段对照，路径相对 services/qc-service/）：
 * - 枚举：qc_result 3 值 / qc_status 4 值 / risk 3 值：app/models/qc.py:25-28
 *   severity 3 值（high/medium/low）：app/models/qc.py:122
 *   speaker（agent/user）：app/models/qc.py:130；source（ac/rule/llm）：app/models/qc.py:132-133
 *   redline_tag 8 值：app/models/qc.py:30-39
 *   人审 action 三值 CONFIRM/CORRECT/DISMISS：app/routers/review.py:21
 *   告警 level 仅 high / status NEW|ACKED：app/models/qc.py:175-176
 * - 扣分档位 high 20 / medium 8 / low 2：app/seeds/qc_rules_v1.py:34（PENALTY_V1）；
 *   客户侧（speaker='user'，含 R13）score_eligible=false → points 记 0 不计入扣分：
 *   app/repositories/quality_repo.py:23-32（points_for_hit）
 * - 错误码 HTTP 分发：app/errors.py:14-29（HTTP_STATUS_BY_CODE：A2002→422、
 *   A2005/A2007→404、A2006→409、A2001/A2003/A2009-A2011→200、A2004/A2008→400）
 * - demo/seed 响应：app/services/demo_seed_service.py:88-93（路由 app/routers/demo.py:29-32）
 * - demo/pending 响应：app/services/demo_seed_service.py:95-114（路由 app/routers/demo.py:35-38）
 * - demo/{session_id}/trace 响应：app/routers/demo.py:55-66；
 *   trace 事件形状：app/services/demo_trace.py:33-55（make_trace_event）；
 *   call_id 可空：app/events/publisher.py:64-74（event_call_id）
 * - records 列表请求/响应：app/routers/records.py:114-158；列表项：records.py:48-61
 * - records 统计请求/响应：app/routers/records.py:91-111；
 *   items 形状（rule_id 维度多 high_count）：app/repositories/quality_repo.py:273-327
 * - records 详情响应：app/routers/records.py:161-182（无 create_time：records.py:172 pop）；
 *   命中项：records.py:64-81（violation_to_hit）与
 *   app/services/replay_service.py:37-54（hit_to_http）同形，统一为 QcHit
 * - replay/raw 请求体/请求头：app/routers/check.py:158-172（RawQcSession check.py:60-77、
 *   RawTurn check.py:49-57、X-Idempotency-Key 必填 check.py:163、X-QC-Force-Rerun check.py:164）
 * - replay/{session_id} 请求体：check.py:43-46（ReplayPathBody，force 默认 false）；
 *   force 生效口径：check.py:139-140（_force_rerun = body.force 或头部为 "true"）
 * - replay 两端点响应：app/services/replay_service.py:85-99（run_result_to_http）
 * - check/batch 请求/响应：app/routers/check.py:187-220（BatchBody check.py:80-87；
 *   上限 QC_BATCH_MAX=100：app/core/config.py:35）
 * - reviews/pending 请求/响应：app/routers/review.py:40-60；
 *   列表项：app/repositories/review_repo.py:67-78（pending_item）
 * - reviews/{qc_record_id} 请求体：app/routers/review.py:24-30（ReviewBody）；
 *   响应：app/services/review_service.py:91-100
 * - alerts 列表请求/响应：app/routers/alerts.py:26-48；列表项：app/repositories/alert_repo.py:87-98
 * - alerts/{alert_id}/ack 响应：app/routers/alerts.py:51-75
 * - rules/active 响应：app/repositories/rule_repo.py:151-162（to_active_payload）
 * - rules/apply 请求体：app/routers/rules.py:18-21（ApplyBody）；
 *   响应：rules.py:52-59；previous_version 可空：app/repositories/rule_repo.py:106/122
 *
 * records 分页签名核实结论（task 1.2）：
 *   app/routers/records.py:116-117 → `page: int = Query(1, ge=1)`、
 *   `page_size: int = Query(20, ge=1, le=100)`，且 records.py:128-129 对 page_size>100
 *   再抛 A2002。与 §5.0.1「page 从 1、page_size 默认 20、上限 100」逐条一致，
 *   无需 SSOT 闭环回填；本文件以后端实际签名为准。
 *
 * 规则（spec「API 层必须逐条对齐 §5.0 C 表」）：snake_case 逐字一致；可选字段
 * 用 `T | null`（后端 None 序列化为 null），不用 undefined；时间一律 ISO 8601
 * 字符串透传不隐式时区改写；UUID 按 string；分页复用 shared 的 PageResult<T>。
 */
import type { PageResult } from '@/shared'

// ── 枚举（字符串字面量联合，与后端逐字一致，前端禁止发明状态）──

/** 风险等级 3 值（app/models/qc.py:28 RISK_LEVEL_VALUES） */
export type QcRiskLevel = 'low' | 'medium' | 'high'

/** 质检状态 4 值（app/models/qc.py:26 QC_STATUS_VALUES） */
export type QcStatus = 'DONE' | 'PENDING_REVIEW' | 'REVIEWED' | 'PARTIAL'

/** 质检结论 3 值（app/models/qc.py:25 QC_RESULT_VALUES） */
export type QcResult = 'compliant' | 'violation' | 'pending_review'

/** 命中严重度 3 值（app/models/qc.py:122） */
export type QcSeverity = 'high' | 'medium' | 'low'

/** 命中来源 3 值（app/models/qc.py:132-133） */
export type QcHitSource = 'ac' | 'rule' | 'llm'

/** 命中说话方 2 值（app/models/qc.py:130）；user 即客户侧 */
export type QcHitSpeaker = 'agent' | 'user'

/** 红线标签 8 值（app/models/qc.py:30-39 REDLINE_TAG_VALUES） */
export type QcRedlineTag =
  | 'threat'
  | 'fake_identity'
  | 'stop_call'
  | 'fake_commit'
  | 'legal_overstate'
  | 'unverified'
  | 'data_fabrication'
  | 'privacy_leak'

/**
 * 人审动作 3 值（app/routers/review.py:21 ReviewAction）。
 * 前端 UI 只暴露 CONFIRM（扣分正确）与 CORRECT（误报/改扣分），不暴露 DISMISS
 * （spec「人工复核必须按后端枚举与扣分状态呈现」）；类型保留三值与后端逐字一致。
 */
export type QcReviewAction = 'CONFIRM' | 'CORRECT' | 'DISMISS'

/** 告警级别：库 CHECK 仅 high（app/models/qc.py:175） */
export type QcAlertLevel = 'high'

/** 告警状态 2 值（app/models/qc.py:176） */
export type QcAlertStatus = 'NEW' | 'ACKED'

/**
 * 扣分档位（app/seeds/qc_rules_v1.py:34 PENALTY_V1）：high 20 / medium 8 / low 2。
 * 实际扣分以生效规则包 penalty 为准（rules/active 响应），此处仅作 v1 基准参照；
 * 客户侧（speaker='user'，含 R13 停止通话）score_eligible=false，points 记 0 不计入
 * （app/repositories/quality_repo.py:23-32）。
 */
export const QC_PENALTY_POINTS = { high: 20, medium: 8, low: 2 } as const satisfies Record<
  QcSeverity,
  number
>

/** R13 等客户侧命中不计入扣分的判定口径：score_eligible=false 即不计入 */
export const QC_R13_RULE_ID = 'R13'

// ── 1. 灌入演示数据 POST /api/v1/qc/demo/seed（demo.py:29-32）──

/** 灌数会话条目（demo_seed_service.py:57-65） */
export interface QcDemoSeedSession {
  session_id: string
  scenario: string
  label: string
  case_id: string
  call_id: string
}

/** 灌数响应（demo_seed_service.py:88-93）；token 为演示用 qc_reviewer JWT */
export interface QcDemoSeedResponse {
  session_ids: string[]
  sessions: QcDemoSeedSession[]
  token: string
  reviewer_id: string
}

// ── 2. 待检会话队列 GET /api/v1/qc/demo/pending（demo.py:35-38）──

/** 待检条目（demo_seed_service.py:105-113）；scenario/label/case_id/call_id 取自 Redis 可空 */
export interface QcDemoPendingItem {
  session_id: string
  scenario: string | null
  label: string | null
  case_id: string | null
  call_id: string | null
}

/** 待检队列响应（demo_seed_service.py:114）；非分页结构，仅 items+total */
export interface QcDemoPendingResponse {
  items: QcDemoPendingItem[]
  total: number
}

// ── 3. 演示全链路 trace GET /api/v1/qc/demo/{session_id}/trace（demo.py:41-67）──

/** trace 事件条目（demo_trace.py:48-55 make_trace_event） */
export interface QcTraceEvent {
  category: string
  name: string
  started_at: string
  duration_ms: number
  status: string
  summary: string
}

/** trace 响应（demo.py:57-66）；langsmith_url 仅开关+run_id 齐全时非空（demo_trace.py:68-70） */
export interface QcTraceResponse {
  session_id: string
  call_id: string | null
  events: QcTraceEvent[]
  langsmith_url: string | null
  mcp_call_qc: string
  mcp_call_qc_note: string
}

// ── 4. 质检记录列表 GET /api/v1/qc/records（records.py:114-158）──

/** 列表请求参数（records.py:116-125：page≥1 默认 1；page_size 默认 20 上限 100；
 *  create_time_from/to 为 datetime，前端传 ISO 8601 字符串） */
export interface QcRecordListQuery {
  page?: number
  page_size?: number
  agent_id?: string
  risk?: QcRiskLevel
  qc_status?: QcStatus
  qc_result?: QcResult
  case_id?: string
  session_id?: string
  create_time_from?: string
  create_time_to?: string
}

/** 列表项（records.py:48-61 record_to_list_item）；grade 退役恒 null（records.py:57，D5） */
export interface QcRecordListItem {
  qc_record_id: string
  session_id: string
  case_id: string | null
  agent_id: string | null
  deduction_total: number
  compliance_score: number
  risk: QcRiskLevel
  grade: null
  qc_result: QcResult
  qc_status: QcStatus
  create_time: string | null
}

/** 列表响应 = 分页信封负载（records.py:150-157，复用 shared PageResult，不新增不改名） */
export type QcRecordListResponse = PageResult<QcRecordListItem>

// ── 5. 质检记录统计 GET /api/v1/qc/records/stats（records.py:91-111）──

/** 统计维度 3 值（records.py:24 GroupByQ）；group_by 为必填查询参数（records.py:93） */
export type QcStatsGroupBy = 'agent' | 'rule_id' | 'day'

/** 统计请求参数（records.py:93-95；from/to 为 datetime 查询别名，前端传 ISO 8601 字符串） */
export interface QcStatsQuery {
  group_by: QcStatsGroupBy
  from?: string
  to?: string
}

/** 统计项（quality_repo.py:273-327）；仅 rule_id 维度带 high_count（quality_repo.py:325） */
export interface QcStatsItem {
  key: string
  count: number
  high_count?: number
}

/** 统计响应（records.py:103-110）；from/to 回显请求窗口，可空 */
export interface QcStatsResponse {
  from: string | null
  to: string | null
  group_by: QcStatsGroupBy
  items: QcStatsItem[]
}

// ── 6. 质检记录详情 GET /api/v1/qc/records/{session_id}（records.py:161-182）──

/**
 * 命中项：详情侧 records.py:64-81（violation_to_hit）与 replay 侧
 * replay_service.py:37-54（hit_to_http）同形，统一为本接口。
 * evidence_text 后端恒返回脱敏字符串（空则为 ''）；points 详情侧恒为 int，
 * replay 侧取自 extras 可空（replay_service.py:50），故类型为 number | null。
 */
export interface QcHit {
  turn_id: string | null
  rule_id: string | null
  word_id: string | null
  severity: QcSeverity
  redline_tag: QcRedlineTag | null
  speaker: QcHitSpeaker
  /** 客户侧（speaker='user'，含 R13）为 false → points 记 0 不计入扣分 */
  score_eligible: boolean
  evidence_text: string
  source: QcHitSource | null
  word: string | null
  points: number | null
  policy_clause: string | null
  policy_text: string | null
  /** RAG 检索条款原文；与 policy_text（静态制度条款）严格分离，禁止混用 */
  kb_snippet: string | null
}

/** 详情响应（records.py:161-182）；在列表项基础上去掉 create_time（records.py:172），
 *  追加 high/med/low 计数、命中明细与 skipped_rules（本通未审/未适用规则） */
export interface QcRecordDetailResponse {
  qc_record_id: string
  session_id: string
  case_id: string | null
  agent_id: string | null
  deduction_total: number
  compliance_score: number
  risk: QcRiskLevel
  grade: null
  qc_result: QcResult
  qc_status: QcStatus
  high_count: number
  med_count: number
  low_count: number
  hits: QcHit[]
  skipped_rules: string[]
}

// ── 7. 会话回放（原始）POST /api/v1/qc/replay/raw（check.py:158-172）──

/** 原始会话单轮（check.py:49-57 RawTurn）；speaker 允许 agent/user/ai/human（后端映射归并） */
export interface QcRawTurn {
  turn_id: string
  speaker: 'agent' | 'user' | 'ai' | 'human'
  text: string
  turn_index: number
  hit_ts?: string | null
  start_offset_ms?: number | null
}

/** 原始回放请求体（check.py:60-77 RawQcSession）；call_ended 恒 true（Literal[True]），
 *  turns 至少 1 条；X-Idempotency-Key 走请求头不在 body（api.ts 注入） */
export interface QcReplayRawRequest {
  session_id: string
  case_id: string
  call_ended: true
  turns: QcRawTurn[]
  call_id?: string | null
  text_ready?: boolean | null
  agent_type?: string | null
  started_at?: string | null
  ended_at?: string | null
  duration_seconds?: number | null
  result?: string | null
  debtor_name?: string | null
  case_snapshot?: Record<string, unknown> | null
  dial_history_24h?: Record<string, unknown> | null
  trace_id?: string | null
}

/** replay 两端点共用响应（replay_service.py:85-99 run_result_to_http）；
 *  字段全部可空（QcRunResult 默认 None）；grade 退役恒 null（D5） */
export interface QcReplayResponse {
  qc_record_id: string | null
  session_id: string | null
  case_id: string | null
  call_id: string | null
  deduction_total: number | null
  compliance_score: number | null
  risk: QcRiskLevel | null
  grade: string | null
  qc_result: QcResult | null
  qc_status: QcStatus | null
  hits: QcHit[]
}

// ── 8. 会话质检触发/回放 POST /api/v1/qc/replay/{session_id}（check.py:175-184）──

/** replay 请求体（check.py:43-46 ReplayPathBody）；force 默认 false，
 *  与 X-QC-Force-Rerun 头等价（check.py:139-140），两者任选其一透传 */
export interface QcReplayRequest {
  force?: boolean
}

// ── 9. 批量质检 POST /api/v1/qc/check/batch（check.py:187-220）──

/** 批量请求体（check.py:80-87 BatchBody）；session_ids 至少 1 条，
 *  后端硬上限 QC_BATCH_MAX=100（config.py:35），前端 UI 上限 10（design D2） */
export interface QcCheckBatchRequest {
  batch_id: string
  session_ids: string[]
}

/** 批量受理条目（check.py:204-214，run_result_to_http 子集） */
export interface QcCheckBatchAcceptedItem {
  qc_record_id: string | null
  session_id: string | null
  deduction_total: number | null
  compliance_score: number | null
  risk: QcRiskLevel | null
  grade: string | null
  qc_result: QcResult | null
  qc_status: QcStatus | null
}

/** 批量拒收条目（check.py:200/217：session_id + 错误码） */
export interface QcCheckBatchRejectedItem {
  session_id: string
  code: string
}

/** 批量响应（check.py:218-220）；整体恒 HTTP 200 成功信封，失败逐条落在 rejected */
export interface QcCheckBatchResponse {
  batch_id: string
  accepted: QcCheckBatchAcceptedItem[]
  rejected: QcCheckBatchRejectedItem[]
}

// ── 10. 待复核队列 GET /api/v1/qc/reviews/pending（review.py:40-60）──

/** 待复核请求参数（review.py:42-43：page≥1 默认 1；page_size 默认 20，>100 抛 A2002） */
export interface QcReviewPendingQuery {
  page?: number
  page_size?: number
}

/** 待复核列表项（review_repo.py:67-78 pending_item）；grade 退役恒 null（D5） */
export interface QcReviewPendingItem {
  qc_record_id: string
  session_id: string
  case_id: string | null
  compliance_score: number
  deduction_total: number
  risk: QcRiskLevel
  grade: string | null
  qc_result: QcResult
  qc_status: QcStatus
}

/** 待复核响应 = 分页信封负载（review.py:50-59，复用 shared PageResult） */
export type QcReviewPendingResponse = PageResult<QcReviewPendingItem>

// ── 11. 提交复核 POST /api/v1/qc/reviews/{qc_record_id}（review.py:63-81）──

/** 复核请求体（review.py:24-30 ReviewBody）；CORRECT 时 revised_deduction 必填 >=0 整数
 *  （缺值/越界后端抛 A2008，review_service.py:102-120）；revised_score 灰度保留非主输入 */
export interface QcReviewSubmitRequest {
  action: QcReviewAction
  revised_deduction?: number | null
  revised_score?: number | null
  reason?: string | null
}

/** 复核响应（review_service.py:91-100）；提交后 qc_status 恒为 REVIEWED */
export interface QcReviewSubmitResponse {
  qc_record_id: string
  action: QcReviewAction
  qc_status: QcStatus
  deduction_total: number
  compliance_score: number
  qc_result: QcResult
  risk: QcRiskLevel
  grade: string | null
}

// ── 12. 告警列表 GET /api/v1/qc/alerts（alerts.py:26-48）──

/** 告警请求参数（alerts.py:27-31：page≥1 默认 1；page_size 默认 20，>100 抛 A2002；
 *  status 仅 NEW|ACKED，其余抛 A2002（alerts.py:37-38）） */
export interface QcAlertListQuery {
  page?: number
  page_size?: number
  status?: QcAlertStatus
  session_id?: string
}

/** 告警列表项（alert_repo.py:87-98）；evidence 恒为脱敏字符串（空则为 ''） */
export interface QcAlertItem {
  alert_id: string
  qc_record_id: string
  session_id: string | null
  level: QcAlertLevel
  status: QcAlertStatus
  evidence: string
  create_time: string | null
}

/** 告警列表响应 = 分页信封负载（alerts.py:45-48，复用 shared PageResult） */
export type QcAlertListResponse = PageResult<QcAlertItem>

// ── 13. 告警确认 POST /api/v1/qc/alerts/{alert_id}/ack（alerts.py:51-75）──

/** 告警确认响应（alerts.py:66-75）；已确认记录重复 ack 返回 A2006（alerts.py:58-59） */
export interface QcAlertAckResponse {
  alert_id: string
  status: QcAlertStatus
  acked_by: string | null
  acked_at: string | null
}

// ── 14. 生效规则列表 GET /api/v1/qc/rules/active（rules.py:31-39）──

/** 生效规则响应（rule_repo.py:151-162 to_active_payload）；penalty/grade_thresholds/
 *  deduction_policy 为 JSONB 透传；无生效版本时返回 A2009（rules.py:37-38） */
export interface QcRulesActiveResponse {
  version: string
  status: string
  penalty: Record<string, number>
  same_word_window_ms: number | null
  grade_thresholds: Record<string, unknown>
  deduction_policy: Record<string, unknown>
  effective_at: string | null
}

// ── 15. 规则热应用 POST /api/v1/qc/rules/apply（rules.py:42-60）──

/** 热应用请求体（rules.py:18-21 ApplyBody）；version 非空串 */
export interface QcRulesApplyRequest {
  version: string
}

/** 热应用响应（rules.py:52-59）；previous_version 无上一版本时为 null（rule_repo.py:122） */
export interface QcRulesApplyResponse {
  version: string
  status: string
  previous_version: string | null
}
