/**
 * RAG 知识库模块 API 层：SSOT §5.0 D 表 9 端点逐条接入，一律 /api/v1 全前缀。
 * 只 import shared/request 与 ./types；全部端点超时只引用 TIMEOUT.normal
 * （SSOT §9.3 / design D1：上传异步入库后响应即时，沿用 normal，不设 upload 常量）；
 * 模块内禁止出现超时数值字面量。响应拦截器已解包信封，故用第二泛型 R 直接声明
 * data 类型。检索请求体禁带 answer 键（spec「answer 字段不下发」，类型层与组装层
 * 双重保证）。current.dict 为 text/plain 非信封端点，走 request.ts TEXT_WHITELIST
 * 既有分支（responseType:'text' + URL 双重校验，拦截器零改动）。
 */
import http, { TIMEOUT } from '@/shared/request'
import type {
  RagCategory,
  RagDocumentDeleteResponse,
  RagDocumentDetailResponse,
  RagDocumentListQuery,
  RagDocumentListResponse,
  RagReindexResponse,
  RagSearchRequest,
  RagSearchResponse,
  RagSensitiveDictResponse,
  RagSensitiveRefreshResponse,
  RagSummaryUpdateRequest,
  RagSummaryUpdateResponse,
  RagUploadResponse,
} from './types'

/** 列表分页默认值与边界（documents.py:130-136：page 默认 1、ge=1；page_size 默认 20、
 *  1≤page_size≤100（ge=1/le=100），越界由前端双向钳制，不依赖后端报错（spec 分页钳制 Scenario） */
const DEFAULT_PAGE = 1
const DEFAULT_PAGE_SIZE = 20
const MAX_PAGE_SIZE = 100

/** 1. 文档列表 GET /api/v1/kb/documents（documents.py:126） */
export function listKbDocuments(
  query: RagDocumentListQuery = {},
): Promise<RagDocumentListResponse> {
  // 双向钳制：page<1 → 1；page_size<1 → 1，>100 → 100（后端 ge/le 约束同源）
  const page = Math.max(query.page ?? DEFAULT_PAGE, 1)
  const pageSize = Math.min(
    Math.max(query.page_size ?? DEFAULT_PAGE_SIZE, 1),
    MAX_PAGE_SIZE,
  )
  return http.get<RagDocumentListResponse, RagDocumentListResponse>('/api/v1/kb/documents', {
    params: {
      page,
      page_size: pageSize,
      ...(query.category ? { category: query.category } : {}),
      ...(query.status ? { status: query.status } : {}),
    },
    timeout: TIMEOUT.normal,
  })
}

/** 2. 上传入库 POST /api/v1/kb/documents（documents.py:58，multipart Form）。
 *  异步入库早返回 {doc_id, status:"processing"}（README §G BREAKING），调用方轮询终态；
 *  Content-Type 由 axios 自动带 multipart boundary，禁止手设；
 *  summary 为空串时不下发（后端 Form 默认 ""：documents.py:62）。 */
export function uploadKbDocument(
  file: File,
  category: RagCategory,
  summary?: string,
): Promise<RagUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('category', category)
  if (summary) {
    form.append('summary', summary)
  }
  return http.post<RagUploadResponse, RagUploadResponse>('/api/v1/kb/documents', form, {
    timeout: TIMEOUT.normal,
  })
}

/** 3. 文档详情 GET /api/v1/kb/documents/{doc_id}（documents.py:149） */
export function getKbDocument(docId: string): Promise<RagDocumentDetailResponse> {
  return http.get<RagDocumentDetailResponse, RagDocumentDetailResponse>(
    `/api/v1/kb/documents/${docId}`,
    { timeout: TIMEOUT.normal },
  )
}

/** 4. 删除文档 DELETE /api/v1/kb/documents/{doc_id}（documents.py:193；
 *  成功信封 data 为 null：documents.py:219） */
export function deleteKbDocument(docId: string): Promise<RagDocumentDeleteResponse> {
  return http.delete<RagDocumentDeleteResponse, RagDocumentDeleteResponse>(
    `/api/v1/kb/documents/${docId}`,
    { timeout: TIMEOUT.normal },
  )
}

/** 5. 重建索引 POST /api/v1/kb/documents/{doc_id}/reindex（documents.py:226，同步）。
 *  同步长任务可能撞 12s 超时/网关 504：后端已先置 processing（documents.py:260），
 *  超时后引导刷新列表查 status 终态，不自动重试（design D7）。 */
export function reindexKbDocument(docId: string): Promise<RagReindexResponse> {
  return http.post<RagReindexResponse, RagReindexResponse>(
    `/api/v1/kb/documents/${docId}/reindex`,
    undefined,
    { timeout: TIMEOUT.normal },
  )
}

/** 6. 摘要编辑 PUT /api/v1/kb/documents/{doc_id}/summary（documents.py:354） */
export function updateKbDocumentSummary(
  docId: string,
  body: RagSummaryUpdateRequest,
): Promise<RagSummaryUpdateResponse> {
  return http.put<RagSummaryUpdateResponse, RagSummaryUpdateResponse>(
    `/api/v1/kb/documents/${docId}/summary`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 7. 混合检索 POST /api/v1/kb/search（search.py:53）。
 *  body 显式逐字段组装：禁带 answer 键（spec「answer 字段不下发」；
 *  类型层 RagSearchRequest 亦无 answer 字段，双重保证）；
 *  category 缺省（「全部」）时不下发（models/search.py:23 可空）。 */
export function searchKb(body: RagSearchRequest): Promise<RagSearchResponse> {
  return http.post<RagSearchResponse, RagSearchResponse>(
    '/api/v1/kb/search',
    {
      query: body.query,
      top_k: body.top_k,
      rewrite: body.rewrite,
      ...(body.category ? { category: body.category } : {}),
    },
    { timeout: TIMEOUT.normal },
  )
}

/** 8. 生效词典 GET /api/v1/sensitive-words/current.dict（sensitive_words.py:28）。
 *  text/plain 非信封：responseType:'text' + URL 命中 request.ts TEXT_WHITELIST
 *  （request.ts:90/125-128），成功返回裸文本字符串；404 由拦截器映射为空串，
 *  前端据此展示空态「词典暂不可用」。拦截器零改动。 */
export function getSensitiveWordsDict(): Promise<RagSensitiveDictResponse> {
  return http.get<RagSensitiveDictResponse, RagSensitiveDictResponse>(
    '/api/v1/sensitive-words/current.dict',
    { responseType: 'text', timeout: TIMEOUT.normal },
  )
}

/** 9. 词库刷新 POST /api/v1/sensitive-words/refresh（sensitive_words.py:49） */
export function refreshSensitiveWords(): Promise<RagSensitiveRefreshResponse> {
  return http.post<RagSensitiveRefreshResponse, RagSensitiveRefreshResponse>(
    '/api/v1/sensitive-words/refresh',
    undefined,
    { timeout: TIMEOUT.normal },
  )
}
