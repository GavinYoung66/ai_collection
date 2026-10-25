/**
 * LLM 用量模块 API 层（DRAFT 契约）：仅 2 个端点，一律 /api/v1 全前缀。
 *
 * - GET /api/v1/llm-usage/summary、GET /api/v1/llm-usage/logs（SSOT §5.0 E:250-251，
 *   均标注「待实现」；design D1：前端先行，后端落地后仅关闭 mock 门控）
 * - 超时只引用 TIMEOUT.normal（12s < 网关 15s），模块内禁止超时数值字面量
 * - 分页双向钳制：page ≥ 1；page_size 1~100（默认 20），不依赖后端报错
 * - 响应拦截器已解包信封，第二泛型直接声明 data 类型
 */
import http, { TIMEOUT } from '@/shared/request'
import type {
  UsageLogListResponse,
  UsageLogsQuery,
  UsageSummaryQuery,
  UsageSummaryResponse,
} from './types'

const DEFAULT_PAGE = 1
const DEFAULT_PAGE_SIZE = 20
const MAX_PAGE_SIZE = 100

/** 1. 用量汇总 GET /api/v1/llm-usage/summary */
export function getUsageSummary(
  query: UsageSummaryQuery,
): Promise<UsageSummaryResponse> {
  return http.get<UsageSummaryResponse, UsageSummaryResponse>(
    '/api/v1/llm-usage/summary',
    {
      params: { start: query.start, end: query.end },
      timeout: TIMEOUT.normal,
    },
  )
}

/** 2. 调用明细 GET /api/v1/llm-usage/logs（可选 status / provider / trace_id 筛选） */
export function listUsageLogs(
  query: UsageLogsQuery,
): Promise<UsageLogListResponse> {
  const page = Math.max(query.page ?? DEFAULT_PAGE, 1)
  const pageSize = Math.min(
    Math.max(query.page_size ?? DEFAULT_PAGE_SIZE, 1),
    MAX_PAGE_SIZE,
  )
  return http.get<UsageLogListResponse, UsageLogListResponse>(
    '/api/v1/llm-usage/logs',
    {
      params: {
        page,
        page_size: pageSize,
        start: query.start,
        end: query.end,
        ...(query.status ? { status: query.status } : {}),
        ...(query.provider ? { provider: query.provider } : {}),
        ...(query.trace_id ? { trace_id: query.trace_id } : {}),
      },
      timeout: TIMEOUT.normal,
    },
  )
}
