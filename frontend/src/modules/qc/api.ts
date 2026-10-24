/**
 * 合规质检模块 API 层：SSOT §5.0 C 表 15 端点逐条接入，一律 /api/v1/qc/* 全前缀。
 * 只 import shared/request 与 ./types；超时只引用 TIMEOUT 常量表
 * （replay/{session_id}、check/batch → TIMEOUT.qcReplay，其余 → TIMEOUT.normal，
 * SSOT §9.3 / design D1）；模块内禁止出现超时数值字面量。响应拦截器已解包信封，
 * 故用第二泛型 R 直接声明 data 类型。
 * replay/raw 必须携带 X-Idempotency-Key 头（check.py:163 Header(..., alias) 必填，
 * 缺失后端按参数校验失败处理），每次调用用 crypto.randomUUID() 新生成；
 * force 重跑与后端 _force_rerun（check.py:139-140）对齐：replay/{session_id} 经
 * body.force 或 X-QC-Force-Rerun 头透传，replay/raw 仅 X-QC-Force-Rerun 头。
 */
import http, { TIMEOUT } from '@/shared/request'
import type {
  QcAlertAckResponse,
  QcAlertListQuery,
  QcAlertListResponse,
  QcCheckBatchRequest,
  QcCheckBatchResponse,
  QcDemoPendingResponse,
  QcDemoSeedResponse,
  QcRecordDetailResponse,
  QcRecordListQuery,
  QcRecordListResponse,
  QcReplayRawRequest,
  QcReplayRequest,
  QcReplayResponse,
  QcReviewPendingQuery,
  QcReviewPendingResponse,
  QcReviewSubmitRequest,
  QcReviewSubmitResponse,
  QcRulesActiveResponse,
  QcRulesApplyRequest,
  QcRulesApplyResponse,
  QcStatsQuery,
  QcStatsResponse,
  QcTraceResponse,
} from './types'

/** 列表分页默认值与上限（records.py:116-117 / review.py:42-43 / alerts.py:28-29：
 *  page≥1 默认 1；page_size 默认 20，超上限后端抛参数校验错误，前端先钳制） */
const DEFAULT_PAGE = 1
const DEFAULT_PAGE_SIZE = 20
const MAX_PAGE_SIZE = 100

/** force 重跑选项：true 时经 X-QC-Force-Rerun 头透传（check.py:139-140 头部口径 "true"） */
export interface QcForceRerunOptions {
  forceRerun?: boolean
}

/** 组装 X-QC-Force-Rerun 头；仅在显式 true 时注入 */
function forceRerunHeaders(options?: QcForceRerunOptions): Record<string, string> {
  return options?.forceRerun ? { 'X-QC-Force-Rerun': 'true' } : {}
}

/** 1. 灌入演示数据 POST /api/v1/qc/demo/seed（demo.py:29） */
export function seedQcDemo(): Promise<QcDemoSeedResponse> {
  return http.post<QcDemoSeedResponse, QcDemoSeedResponse>('/api/v1/qc/demo/seed', undefined, {
    timeout: TIMEOUT.normal,
  })
}

/** 2. 待检会话队列 GET /api/v1/qc/demo/pending（demo.py:35） */
export function listQcDemoPending(): Promise<QcDemoPendingResponse> {
  return http.get<QcDemoPendingResponse, QcDemoPendingResponse>('/api/v1/qc/demo/pending', {
    timeout: TIMEOUT.normal,
  })
}

/** 3. 演示全链路 trace GET /api/v1/qc/demo/{session_id}/trace（demo.py:41） */
export function getQcDemoTrace(sessionId: string): Promise<QcTraceResponse> {
  return http.get<QcTraceResponse, QcTraceResponse>(`/api/v1/qc/demo/${sessionId}/trace`, {
    timeout: TIMEOUT.normal,
  })
}

/** 4. 质检记录列表 GET /api/v1/qc/records（records.py:114） */
export function listQcRecords(query: QcRecordListQuery = {}): Promise<QcRecordListResponse> {
  const page = query.page ?? DEFAULT_PAGE
  const pageSize = Math.min(query.page_size ?? DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
  return http.get<QcRecordListResponse, QcRecordListResponse>('/api/v1/qc/records', {
    params: {
      page,
      page_size: pageSize,
      ...(query.agent_id ? { agent_id: query.agent_id } : {}),
      ...(query.risk ? { risk: query.risk } : {}),
      ...(query.qc_status ? { qc_status: query.qc_status } : {}),
      ...(query.qc_result ? { qc_result: query.qc_result } : {}),
      ...(query.case_id ? { case_id: query.case_id } : {}),
      ...(query.session_id ? { session_id: query.session_id } : {}),
      ...(query.create_time_from ? { create_time_from: query.create_time_from } : {}),
      ...(query.create_time_to ? { create_time_to: query.create_time_to } : {}),
    },
    timeout: TIMEOUT.normal,
  })
}

/** 5. 质检记录统计 GET /api/v1/qc/records/stats（records.py:91；group_by 必填） */
export function getQcRecordStats(query: QcStatsQuery): Promise<QcStatsResponse> {
  return http.get<QcStatsResponse, QcStatsResponse>('/api/v1/qc/records/stats', {
    params: {
      group_by: query.group_by,
      ...(query.from ? { from: query.from } : {}),
      ...(query.to ? { to: query.to } : {}),
    },
    timeout: TIMEOUT.normal,
  })
}

/** 6. 质检记录详情 GET /api/v1/qc/records/{session_id}（records.py:161） */
export function getQcRecord(sessionId: string): Promise<QcRecordDetailResponse> {
  return http.get<QcRecordDetailResponse, QcRecordDetailResponse>(
    `/api/v1/qc/records/${sessionId}`,
    { timeout: TIMEOUT.normal },
  )
}

/** 7. 会话回放（原始）POST /api/v1/qc/replay/raw（check.py:158）。
 *  每次调用必须携带新生成的 X-Idempotency-Key（UUID）；force 重跑仅走
 *  X-QC-Force-Rerun 头（check.py:163-164/169）。同步含 LLM 复核，与
 *  replay/{session_id} 同档用 TIMEOUT.qcReplay（SSOT §5.0 C 表 L218 / §9.3，
 *  网关正则 location `^/api/v1/qc/replay/` 120s 前缀覆盖本端点）。 */
export function replayQcRaw(
  body: QcReplayRawRequest,
  options?: QcForceRerunOptions,
): Promise<QcReplayResponse> {
  return http.post<QcReplayResponse, QcReplayResponse>('/api/v1/qc/replay/raw', body, {
    headers: {
      'X-Idempotency-Key': crypto.randomUUID(),
      ...forceRerunHeaders(options),
    },
    timeout: TIMEOUT.qcReplay,
  })
}

/** 8. 会话质检触发/回放 POST /api/v1/qc/replay/{session_id}（check.py:175，长请求 110s）。
 *  force 重跑可经 body.force 或 X-QC-Force-Rerun 头透传（check.py:139-140/180）。 */
export function replayQcSession(
  sessionId: string,
  body: QcReplayRequest = {},
  options?: QcForceRerunOptions,
): Promise<QcReplayResponse> {
  return http.post<QcReplayResponse, QcReplayResponse>(
    `/api/v1/qc/replay/${sessionId}`,
    body,
    {
      headers: forceRerunHeaders(options),
      timeout: TIMEOUT.qcReplay,
    },
  )
}

/** 9. 批量质检 POST /api/v1/qc/check/batch（check.py:187，长请求 110s） */
export function checkQcBatch(body: QcCheckBatchRequest): Promise<QcCheckBatchResponse> {
  return http.post<QcCheckBatchResponse, QcCheckBatchResponse>(
    '/api/v1/qc/check/batch',
    body,
    { timeout: TIMEOUT.qcReplay },
  )
}

/** 10. 待复核队列 GET /api/v1/qc/reviews/pending（review.py:40） */
export function listQcReviewsPending(
  query: QcReviewPendingQuery = {},
): Promise<QcReviewPendingResponse> {
  const page = query.page ?? DEFAULT_PAGE
  const pageSize = Math.min(query.page_size ?? DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
  return http.get<QcReviewPendingResponse, QcReviewPendingResponse>(
    '/api/v1/qc/reviews/pending',
    { params: { page, page_size: pageSize }, timeout: TIMEOUT.normal },
  )
}

/** 11. 提交复核 POST /api/v1/qc/reviews/{qc_record_id}（review.py:63，路径参数为 qc_record_id） */
export function submitQcReview(
  qcRecordId: string,
  body: QcReviewSubmitRequest,
): Promise<QcReviewSubmitResponse> {
  return http.post<QcReviewSubmitResponse, QcReviewSubmitResponse>(
    `/api/v1/qc/reviews/${qcRecordId}`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 12. 告警列表 GET /api/v1/qc/alerts（alerts.py:26） */
export function listQcAlerts(query: QcAlertListQuery = {}): Promise<QcAlertListResponse> {
  const page = query.page ?? DEFAULT_PAGE
  const pageSize = Math.min(query.page_size ?? DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
  return http.get<QcAlertListResponse, QcAlertListResponse>('/api/v1/qc/alerts', {
    params: {
      page,
      page_size: pageSize,
      ...(query.status ? { status: query.status } : {}),
      ...(query.session_id ? { session_id: query.session_id } : {}),
    },
    timeout: TIMEOUT.normal,
  })
}

/** 13. 告警确认 POST /api/v1/qc/alerts/{alert_id}/ack（alerts.py:51） */
export function ackQcAlert(alertId: string): Promise<QcAlertAckResponse> {
  return http.post<QcAlertAckResponse, QcAlertAckResponse>(
    `/api/v1/qc/alerts/${alertId}/ack`,
    undefined,
    { timeout: TIMEOUT.normal },
  )
}

/** 14. 生效规则列表 GET /api/v1/qc/rules/active（rules.py:31） */
export function getQcRulesActive(): Promise<QcRulesActiveResponse> {
  return http.get<QcRulesActiveResponse, QcRulesActiveResponse>('/api/v1/qc/rules/active', {
    timeout: TIMEOUT.normal,
  })
}

/** 15. 规则热应用 POST /api/v1/qc/rules/apply（rules.py:42） */
export function applyQcRules(body: QcRulesApplyRequest): Promise<QcRulesApplyResponse> {
  return http.post<QcRulesApplyResponse, QcRulesApplyResponse>(
    '/api/v1/qc/rules/apply',
    body,
    { timeout: TIMEOUT.normal },
  )
}
