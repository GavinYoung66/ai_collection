/**
 * LLM 用量模块类型契约（DRAFT：后端 2 端点未实现，字段锚定
 * `common/db/models/llm_log.py:14-27`，不新增列名）。
 *
 * 字段来源（llm_log.py 行号）：
 * - id:14 / trace_id:15 / session_id:16 / provider:17 / model:18
 * - token_usage:21（JSONB）/ latency_ms:22 / confidence:23
 * - status:25 / error_msg:26 / create_time:27
 *
 * status 取值以实际写入为准（见 design「status 枚举已知不一致」）：
 * - `ok`：llm_reviewer.py:466
 * - `qc_review_exemption`：llm_reviewer.py:390/:407/:425/:436/:449/:459
 * - `skipped`：llm_reviewer.py:397
 * - `success/failed/timeout/circuit_open`：仅 llm_log.py:25 列注释的计划值，作 DRAFT 候选保留；
 *   前端禁止映射改名，列表原样透出（spec R5）。
 *
 * 规则：snake_case 逐字一致；后端可能返回 null 的字段用 `T | null`；时间一律 ISO 8601 字符串透传。
 */
import type { PageResult } from '@/shared'

/** 调用状态：实际写入值（基线）+ 列注释计划值（DRAFT 候选），不做前端映射 */
export type UsageLogStatus =
  | 'ok'
  | 'qc_review_exemption'
  | 'skipped'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'circuit_open'

/** 时间范围快捷项（spec R3：近 1 天 / 7 天 / 30 天 / 自定义） */
export type UsageTimeRangeKey = '1d' | '7d' | '30d' | 'custom'

/** 时间范围（ISO 8601，左闭右开；分桶边界同为 DRAFT 待确认） */
export interface UsageTimeRange {
  start: string
  end: string
}

/** GET /api/v1/llm-usage/summary 查询参数 */
export type UsageSummaryQuery = UsageTimeRange

/** 四项核心指标（spec R3） */
export interface UsageTotals {
  /** count(*)，看板空态判据（calls === 0） */
  calls: number
  /** sum(token_usage.total_tokens)，来源 llm_log.py:21 */
  tokens: number
  /** avg(latency_ms)，来源 :22；null 是否排除待后端确认 */
  avg_latency_ms: number | null
  /** 失败占比，0~1；分母待后端确认 */
  failure_rate: number
}

/** 按模型分布（来源 :18） */
export interface UsageModelStat {
  model: string
  calls: number
  tokens: number
}

/** 按服务分布：后端无 svc 列，缺失时整块隐藏（design D4） */
export interface UsageServiceStat {
  service: string
  calls: number
}

/** 趋势点：group by create_time（:27）分桶 */
export interface UsageTrendPoint {
  bucket_start: string
  calls: number
  failure_rate: number
}

/** 韧性计数：缺失时整块隐藏（design D4），前端不自算 */
export interface UsageResilience {
  rate_limited: number
  breaker_open: number
  fallback: number
}

/** GET /api/v1/llm-usage/summary 响应 data */
export interface UsageSummaryResponse {
  totals: UsageTotals
  by_model: UsageModelStat[]
  /** 结构性缺失（无 svc 列），存在且非空才渲染 */
  by_service?: UsageServiceStat[]
  trend: UsageTrendPoint[]
  resilience?: UsageResilience
}

/** token 用量（JSONB，:21）：字段缺失或整体为 null 均视为无数据 */
export interface UsageTokenUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

/** 调用明细项（逐列映射 llm_log.py） */
export interface UsageLogItem {
  id: string
  trace_id: string
  session_id: string | null
  provider: string
  model: string | null
  token_usage: UsageTokenUsage | null
  latency_ms: number | null
  confidence: number | null
  /** 原样透出后端值，禁止前端改写（spec R5） */
  status: UsageLogStatus
  error_msg: string | null
  create_time: string
}

/** GET /api/v1/llm-usage/logs 查询参数；status/provider/trace_id 为可选筛选 */
export interface UsageLogsQuery extends UsageTimeRange {
  page?: number
  page_size?: number
  status?: UsageLogStatus
  provider?: string
  trace_id?: string
}

/** GET /api/v1/llm-usage/logs 响应 data（分页信封与后端 PageResponse 一致） */
export type UsageLogListResponse = PageResult<UsageLogItem>
