/**
 * 智能投诉模块类型契约（唯一来源：complaint-service 后端源码，D6）。
 *
 * 核实记录（2026-10-23 逐字段对照，路径相对仓库根）：
 * - TaskStatus 11 值：services/complaint-service/app/models/state.py:23-36
 *   终态集合 = resolved / closed / archived（state.py:34-36）
 *   等待人工集合 = pending_review / pending_senior_review / pending_callback（state.py:31-33）
 * - VerdictType 4 值（不含已废弃 normal）：state.py:39-45
 * - ReviewAction 6 值：state.py:88-96
 * - 定责三值 = VerdictType 去掉 pending（establishment 不可为 pending）：app/api/schemas/complaint.py:170-174
 * - 列表请求参数（page 从 1 起、page_size 默认 20 上限 100、task_status 重复查询参数）：app/api/routes/complaints.py:87-96
 * - 列表项 / 分页响应：app/api/schemas/complaint.py:42-54
 * - 详情响应：app/api/schemas/complaint.py:59-77（组装见 routes/complaints.py:136-171）
 * - judge 请求/响应：app/api/schemas/complaint.py:82-91
 * - status 响应：app/api/schemas/complaint.py:96-99
 * - verdict 响应：app/api/schemas/complaint.py:104-115
 * - agent-logs 响应：app/api/schemas/complaint.py:120-130
 * - trace 响应：app/api/schemas/complaint.py:135-147
 * - review 请求/响应：app/api/schemas/complaint.py:152-165
 * - disposition/verdict 请求/响应：app/api/schemas/complaint.py:170-187
 * - disposition/callback 请求/响应：app/api/schemas/complaint.py:190-200
 * - close 请求/响应：app/api/schemas/complaint.py:205-213
 * - demo/seed 响应：app/api/schemas/complaint.py:14-22
 * - events 响应：app/api/routes/complaints.py:123-133（无独立 schema，{items:[{topic,payload}],total}）
 * - stats 响应：app/api/schemas/complaint.py:235-241（路由 app/api/routes/stats.py:18-30）
 * - mode 请求/响应：app/api/schemas/complaint.py:218-230（路由 app/api/routes/mode.py:26-36）
 *
 * 规则（spec「API 层必须逐条对齐 §5.0 B 表」）：snake_case 逐字一致；响应可空字段
 * 用 `T | null`（后端 model_dump 把 None 序列化为 null），不用 undefined；时间一律
 * ISO 8601 字符串透传不隐式时区改写；UUID 按 string；分页复用 shared 的 PageResult<T>。
 */
import type { PageResult } from '@/shared/types/envelope'

// ── 枚举（字符串字面量联合，与后端逐字一致，前端禁止发明状态）──

/** 任务状态 11 值（state.py:23-36） */
export type TaskStatus =
  | 'pending'
  | 'evidence_gathering'
  | 'reflection_retry'
  | 'judging'
  | 'draft_generated'
  | 'pending_review'
  | 'pending_senior_review'
  | 'pending_callback'
  | 'resolved'
  | 'closed'
  | 'archived'

/** 任务状态 → 中文标签（仅前端展示用；请求/响应仍用 TaskStatus 原值） */
export const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: '待处理',
  evidence_gathering: '证据收集中',
  reflection_retry: '补充取证',
  judging: '研判中',
  draft_generated: '草稿已生成',
  pending_review: '待人工复核',
  pending_senior_review: '待高级复核',
  pending_callback: '待回电',
  resolved: '已解决',
  closed: '已关单',
  archived: '已归档',
}

/** 终态集合（state.py:34-36）：到达即停止轮询并补拉 verdict/agent-logs */
export const TERMINAL_TASK_STATUS = ['resolved', 'closed', 'archived'] as const satisfies readonly TaskStatus[]

/** 等待人工集合（state.py:31-33）：低频继续或停止并提示 */
export const WAITING_HUMAN_TASK_STATUS = [
  'pending_review',
  'pending_senior_review',
  'pending_callback',
] as const satisfies readonly TaskStatus[]

/** 判责类型 4 值，不含 normal（state.py:39-45） */
export type VerdictType =
  | 'agent_violation'
  | 'user_misunderstanding'
  | 'compliant_but_poor_experience'
  | 'pending'

/** 定责三值：establishment 不可为 pending（schemas/complaint.py:170-174） */
export type EstablishmentVerdict = Exclude<VerdictType, 'pending'>

/** 判责类型 → 中文标签（仅前端展示用；请求体仍传 VerdictType 原值） */
export const VERDICT_LABELS: Record<VerdictType, string> = {
  agent_violation: '坐实违规',
  user_misunderstanding: '用户误解',
  compliant_but_poor_experience: '合规但体验差',
  pending: '待定',
}

/** 定责下拉选项：label 中文展示，value 传后端原值 */
export const ESTABLISHMENT_OPTIONS: readonly { value: EstablishmentVerdict; label: string }[] = [
  { value: 'agent_violation', label: VERDICT_LABELS.agent_violation },
  { value: 'user_misunderstanding', label: VERDICT_LABELS.user_misunderstanding },
  { value: 'compliant_but_poor_experience', label: VERDICT_LABELS.compliant_but_poor_experience },
]

/** 人工复核动作 6 值（state.py:88-96） */
export type ReviewAction =
  | 'approve'
  | 'modify_verdict'
  | 'modify_response'
  | 'reject_rejudge'
  | 'add_evidence'
  | 'escalate'

/** 复核动作 → 中文标签（仅展示用；请求体仍传 ReviewAction 原值） */
export const REVIEW_ACTION_LABELS: Record<ReviewAction, string> = {
  approve: '通过',
  modify_verdict: '改判',
  modify_response: '改回应',
  reject_rejudge: '驳回重研判',
  add_evidence: '补充证据',
  escalate: '升级高级复核',
}

/** 复核动作下拉选项：label 中文，value 传后端原值 */
export const REVIEW_ACTION_OPTIONS: readonly { value: ReviewAction; label: string }[] = (
  Object.keys(REVIEW_ACTION_LABELS) as ReviewAction[]
).map((value) => ({ value, label: REVIEW_ACTION_LABELS[value] }))

/** 需要填理由的复核动作（schemas/complaint.py:152-159） */
export const REVIEW_ACTIONS_NEED_REASON: readonly ReviewAction[] = [
  'modify_verdict', 'modify_response', 'reject_rejudge', 'add_evidence',
]

/** 运行模式（schemas/complaint.py:219 description：single_agent | multi_agent） */
export type RunMode = 'single_agent' | 'multi_agent'

// ── 列表 ──

/** 列表请求参数（routes/complaints.py:89-92：page≥1 默认 1；page_size 默认 20 上限 100；
 *  task_status 为重复查询参数，后端 list[TaskStatus]，复核工作台按待复核集合过滤） */
export interface ComplaintListQuery {
  page?: number
  page_size?: number
  task_status?: TaskStatus[]
}

/** 列表项（schemas/complaint.py:42-47） */
export interface ComplaintListItem {
  complaint_id: string
  complaint_text: string
  task_status: TaskStatus
  verdict: VerdictType | null
  created_at: string
}

/** 列表响应 = 分页信封负载（schemas/complaint.py:50-54，复用 shared PageResult，不新增不改名） */
export type ComplaintListResponse = PageResult<ComplaintListItem>

// ── 手工建单 ──

/** 建单请求（schemas/complaint.py:27-31）；channel 后端默认 'phone' */
export interface ComplaintCreateRequest {
  complaint_text: string
  session_id?: string
  case_id?: string
  channel?: string
}

/** 建单响应（schemas/complaint.py:34-37） */
export interface ComplaintCreateResponse {
  complaint_id: string
  task_status: TaskStatus
  created_at: string
}

// ── 演示灌数 ──

/** 演示工单条目（schemas/complaint.py:14-17） */
export interface DemoSeedItem {
  complaint_id: string
  scenario: string
  task_status: TaskStatus
}

/** 演示灌数响应（schemas/complaint.py:20-22） */
export interface DemoSeedResponse {
  complaint_ids: string[]
  items: DemoSeedItem[]
}

// ── 事件列表（演示用）──

/** 事件条目（routes/complaints.py:132：topic + payload） */
export interface ComplaintEventItem {
  topic: string
  payload: Record<string, unknown>
}

/** 事件列表响应（routes/complaints.py:133）；topic 为可选查询参数（routes/complaints.py:126） */
export interface ComplaintEventsResponse {
  items: ComplaintEventItem[]
  total: number
}

// ── 详情 ──

/** 详情响应（schemas/complaint.py:59-77） */
export interface ComplaintDetailResponse {
  complaint_id: string
  complaint_text: string
  task_status: TaskStatus
  verdict: VerdictType | null
  ai_verdict: VerdictType | null
  disposition_verdict: VerdictType | null
  disposition_reason: string | null
  callback_scheduled: boolean
  ordered_evidence: Record<string, unknown>[]
  evidence_refs: string[]
  confidence: number | null
  response_draft: string | null
  created_at: string
  updated_at: string
}

// ── judge ──

/** judge 请求（schemas/complaint.py:82-83）；run_graph 默认 false（stub） */
export interface JudgeRequest {
  run_graph?: boolean
}

/** judge 响应（schemas/complaint.py:86-91） */
export interface JudgeResponse {
  complaint_id: string
  task_status: TaskStatus
  verdict: VerdictType | null
  thread_id: string | null
  message: string
}

// ── status ──

/** status 响应（schemas/complaint.py:96-99） */
export interface StatusResponse {
  complaint_id: string
  task_status: TaskStatus
  progress: number
}

// ── verdict ──

/** verdict 响应（schemas/complaint.py:104-115） */
export interface VerdictResponse {
  complaint_id: string
  verdict: VerdictType | null
  confidence: number | null
  response_draft: string | null
  regulation_refs: string[]
  rag_source_ids: string[]
  evidence_refs: string[]
}

// ── agent logs ──

/** agent 日志条目（schemas/complaint.py:120-125） */
export interface AgentLogItem {
  node_name: string
  input_summary: string
  output_summary: string
  started_at: string
  duration_ms: number
}

/** agent 日志响应（schemas/complaint.py:128-130） */
export interface AgentLogsResponse {
  complaint_id: string
  logs: AgentLogItem[]
}

// ── trace ──

/** trace 事件条目（schemas/complaint.py:135-141） */
export interface TraceEventItem {
  category: string
  name: string
  started_at: string
  duration_ms: number
  status: string
  summary: string
}

/** trace 响应（schemas/complaint.py:144-147） */
export interface TraceResponse {
  complaint_id: string
  events: TraceEventItem[]
  langsmith_url: string | null
}

// ── 定责确认 ──

/** 定责请求（schemas/complaint.py:170-180）；reason 在与 AI 初选不一致时必填 */
export interface DispositionVerdictRequest {
  establishment: EstablishmentVerdict
  reason?: string
  reviewer?: string
  comment?: string
}

/** 定责响应（schemas/complaint.py:183-187） */
export interface DispositionVerdictResponse {
  complaint_id: string
  task_status: TaskStatus
  establishment: VerdictType
  ai_verdict: VerdictType | null
}

// ── 回电登记 ──

/** 回电登记请求（schemas/complaint.py:190-193） */
export interface DispositionCallbackRequest {
  reviewer?: string
  note?: string
  comment?: string
}

/** 回电登记响应（schemas/complaint.py:196-200） */
export interface DispositionCallbackResponse {
  complaint_id: string
  task_status: TaskStatus
  callback_scheduled: boolean
  establishment: VerdictType | null
}

// ── 人工复核 ──

/** 复核请求（schemas/complaint.py:152-159）；reason 在 reject_rejudge/modify_* 时必填 */
export interface ReviewRequest {
  review_action: ReviewAction
  reviewer?: string
  comment?: string
  reason?: string
}

/** 复核响应（schemas/complaint.py:162-165） */
export interface ReviewResponse {
  complaint_id: string
  task_status: TaskStatus
  review_action: ReviewAction
}

// ── 关单 ──

/** 关单请求（schemas/complaint.py:205-207） */
export interface CloseRequest {
  closer?: string
  reason?: string
}

/** 关单响应（schemas/complaint.py:210-213） */
export interface CloseResponse {
  complaint_id: string
  task_status: TaskStatus
  event_published: boolean
}

// ── 统计 ──

/** 统计响应（schemas/complaint.py:235-241）；by_verdict 键为 VerdictType 四值（含 pending） */
export interface StatsResponse {
  total: number
  by_verdict: Partial<Record<VerdictType, number>>
  avg_processing_minutes: number
  evidence_insufficient_rate: number
  mcp_failure_rate: number
  ai_human_consistency_rate: number
}

// ── 运行模式切换 ──

/** 模式切换请求（schemas/complaint.py:218-225）；signers 双签须含 ops + tech，至少 1 人 */
export interface ModeSwitchRequest {
  mode: RunMode
  gray_percent?: number
  signers: string[]
}

/** 模式切换响应（schemas/complaint.py:228-230） */
export interface ModeSwitchResponse {
  mode: string
  gray_percent: number
}
