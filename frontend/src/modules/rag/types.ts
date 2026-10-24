/**
 * RAG 知识库模块类型契约（唯一来源：rag-service 后端源码，design D1）。
 *
 * 核实记录（2026-10-24 逐字段对照，路径相对 services/rag-service/）：
 * - 分类 4 值（regulation/script/case/other）：app/api/documents.py:38（_VALID_CATEGORIES）
 * - 文档状态 4 值：processing（app/repositories/doc_repo.py:36 入库初始值）、
 *   active（app/api/documents.py:340 reindex 终态）、failed（doc_repo.py:118 mark_failed）、
 *   archived（doc_repo.py:208 soft_delete）；ORM 列默认：app/models/orm.py:20
 * - 入库阶段 4 值与进度映射 parse=25/body_index=50/asset_index=75/done=100：
 *   doc_repo.py:10-15（STAGE_PROGRESS）；README §G BREAKING 表（无资产时跳过 asset_index）
 * - 摘要长度上限 KB_SUMMARY_MAX_LEN=200：app/rag_settings.py:47；
 *   超限后端抛 B0001（documents.py:78-82）
 * - 文档列表请求/响应：app/api/documents.py:126-146（page_size 别名 size：documents.py:131-132，
 *   前端只用 page_size）；列表项形状：app/repositories/doc_repo.py:183-195
 *   （ingest_* 三字段经 _ingest_fields 注入：doc_repo.py:60-65/193）
 * - 上传请求（multipart Form：file/category/summary）与响应：app/api/documents.py:58-116
 *   （异步入库早返回 {doc_id, status:"processing"}，README §G BREAKING）
 * - 文档详情响应：app/api/documents.py:149-190（assets 项：documents.py:181-189）
 * - 删除响应：app/api/documents.py:193-223（success_response(None) → data 为 null，documents.py:219）
 * - 重建索引响应：app/api/documents.py:346-351（同步；assets_rebuilt 恒 false，documents.py:350）
 * - 摘要编辑请求体：app/models/document.py:49-59（SummaryUpdateRequest）；
 *   响应：app/api/documents.py:387-390
 * - 检索请求体：app/models/search.py:21-34（query 1~500 字、category 枚举、top_k 1~20 默认 5、
 *   rewrite 默认 false；answer 字段本模块恒不下发，spec「answer 字段不下发」）
 * - 检索响应：app/models/search.py:75-100（SearchResponse）；结果项：models/search.py:44-72
 *   （SourceItem）；资产预览：models/search.py:5-18（AssetPreview）
 * - 生效词典：app/api/sensitive_words.py:28-46（text/plain 非信封；均失败抛 HTTP 404）
 * - 词库刷新响应：app/api/sensitive_words.py:49-63（{version}；version 为 Redis INCR 整数，
 *   app/services/sensitive_words_sync.py:83-131）
 *
 * 规则（spec「API 层必须逐条对齐 §5.0 D 表」）：snake_case 逐字一致；可选字段
 * 用 `T | null`（后端 None 序列化为 null），不用 undefined；时间一律 ISO 8601
 * 字符串透传不隐式时区改写；UUID 按 string；分页复用 shared 的 PageResult<T>。
 */
import type { PageResult } from '@/shared'

// ── 枚举（字符串字面量联合，与后端逐字一致，前端禁止发明状态）──

/** 文档分类 4 值（documents.py:38 _VALID_CATEGORIES） */
export type RagCategory = 'regulation' | 'script' | 'case' | 'other'

/**
 * 文档状态 4 值：processing（doc_repo.py:36 入库初始）/ active（documents.py:340
 * reindex 终态、入库后台完成态）/ failed（doc_repo.py:118 mark_failed）/
 * archived（doc_repo.py:208 soft_delete）。
 */
export type RagDocStatus = 'active' | 'processing' | 'failed' | 'archived'

/** 入库阶段 4 值（doc_repo.py:10-15 STAGE_PROGRESS 键）；mark_failed 后置 null（doc_repo.py:118） */
export type RagIngestStage = 'parse' | 'body_index' | 'asset_index' | 'done'

/** 摘要长度上限（rag_settings.py:47 KB_SUMMARY_MAX_LEN=200）；前端校验与服务端一致 */
export const KB_SUMMARY_MAX_LEN = 200

/** 分类 → 中文文案映射（对齐 formatters.py:22-27 CATEGORY_CODE_TO_ZH，前端不发明分类；
 *  展示映射常量放 types.ts 同 complaint STATUS_LABELS 先例） */
export const RAG_CATEGORY_LABELS: Record<RagCategory, string> = {
  regulation: '制度法规',
  script: '催收话术',
  case: '案例',
  other: '其他',
}

// ── 1. 文档列表 GET /api/v1/kb/documents（documents.py:126-146）──

/** 列表请求参数（documents.py:128-132：category/status 可选；page≥1 默认 1；
 *  page_size 默认 20、上限 100（documents.py:131/136），前端先钳制（spec 分页钳制 Scenario）；
 *  后端另有别名 size（documents.py:132），前端不使用） */
export interface RagDocumentListQuery {
  /** documents.py:128 */
  category?: RagCategory
  /** documents.py:129 */
  status?: RagDocStatus
  /** documents.py:130（Query(1, ge=1)） */
  page?: number
  /** documents.py:131（Query(None, ge=1, le=100)；缺省后端落 20：documents.py:136） */
  page_size?: number
}

/** 列表项（doc_repo.py:185-193；ingest_* 三字段经 _ingest_fields 注入：doc_repo.py:60-65） */
export interface RagDocumentListItem {
  /** doc_repo.py:186（str(doc.id)，UUID 字符串） */
  id: string
  /** doc_repo.py:187 */
  title: string
  /** doc_repo.py:188 */
  category: RagCategory
  /** doc_repo.py:189 */
  status: RagDocStatus
  /** doc_repo.py:190（chunk 批量聚合计数，无 chunk 为 0） */
  chunks_count: number
  /** doc_repo.py:191（create_time.isoformat()；为空时后端给空串 ""） */
  create_time: string
  /** doc_repo.py:62（可空；mark_failed 后置 null：doc_repo.py:118） */
  ingest_stage: RagIngestStage | null
  /** doc_repo.py:63（可空；进度按 STAGE_PROGRESS 映射） */
  ingest_progress: number | null
  /** doc_repo.py:64（可空；failed 时读此定位原因，README §G） */
  ingest_error: string | null
}

/** 列表响应 = 分页信封负载（documents.py:141-146，复用 shared PageResult，不新增不改名） */
export type RagDocumentListResponse = PageResult<RagDocumentListItem>

// ── 2. 上传入库 POST /api/v1/kb/documents（documents.py:58-123）──

/**
 * 上传响应（documents.py:113-116）：异步入库早返回（README §G BREAKING），
 * status 恒为 "processing"，调用方轮询列表/详情直至 active/failed。
 */
export interface RagUploadResponse {
  /** documents.py:114 */
  doc_id: string
  /** documents.py:115（恒 "processing"，异步入库早返回） */
  status: 'processing'
}

// ── 3. 文档详情 GET /api/v1/kb/documents/{doc_id}（documents.py:149-190）──

/** 详情资产项（documents.py:181-189，kb_doc_assets 元数据透传） */
export interface RagDocumentAsset {
  /** documents.py:183（image/table 等，asset_type 枚举后端不收敛，按 string 透传） */
  asset_type: string
  /** documents.py:184 */
  page: number
  /** documents.py:185（相对 KB_ASSETS_DIR 路径） */
  storage_path: string
  /** documents.py:186（可空） */
  mime_type: string | null
}

/** 详情响应（documents.py:167-190）；create_time/update_time 为空时后端给空串 ""（documents.py:175-176） */
export interface RagDocumentDetailResponse {
  /** documents.py:168 */
  id: string
  /** documents.py:169 */
  title: string
  /** documents.py:170 */
  category: RagCategory
  /** documents.py:171（PG 正文全文） */
  content: string
  /** documents.py:172 */
  status: RagDocStatus
  /** documents.py:173（orm.py:19 默认 1） */
  version: number
  /** documents.py:174（get_chunks_count 实时计数） */
  chunks_count: number
  /** documents.py:175（isoformat 或 ""） */
  create_time: string
  /** documents.py:176（isoformat 或 ""） */
  update_time: string
  /** documents.py:177（可空；PG kb_documents.summary 权威源，document.py:40-46） */
  summary: string | null
  /** documents.py:178（可空） */
  ingest_stage: RagIngestStage | null
  /** documents.py:179（可空） */
  ingest_progress: number | null
  /** documents.py:180（可空；failed 时读此定位原因） */
  ingest_error: string | null
  /** documents.py:181-189（无资产时为空数组） */
  assets: RagDocumentAsset[]
}

// ── 4. 删除文档 DELETE /api/v1/kb/documents/{doc_id}（documents.py:193-223）──

/** 删除响应：success_response(None, "文档已删除")（documents.py:219）→ data 恒 null */
export type RagDocumentDeleteResponse = null

// ── 5. 重建索引 POST /api/v1/kb/documents/{doc_id}/reindex（documents.py:226-351）──

/** 重建索引响应（documents.py:346-351）；同步长任务（超时引导刷新查终态，design D7）；
 *  仅按 PG 正文重切，不补全表格/图片资产（README §G reindex 行） */
export interface RagReindexResponse {
  /** documents.py:347 */
  doc_id: string
  /** documents.py:348 */
  chunks_count: number
  /** documents.py:349（成功时恒 "active"） */
  status: 'active'
  /** documents.py:350（恒 false：不重建多模态资产） */
  assets_rebuilt: false
}

// ── 6. 摘要编辑 PUT /api/v1/kb/documents/{doc_id}/summary（documents.py:354-390）──

/** 摘要编辑请求体（document.py:49-59 SummaryUpdateRequest）；
 *  长度上限 KB_SUMMARY_MAX_LEN=200（rag_settings.py:47），超限后端 B0001（documents.py:78-82） */
export interface RagSummaryUpdateRequest {
  /** document.py:56-59（必填，max_length=KB_SUMMARY_MAX_LEN） */
  summary: string
}

/** 摘要编辑响应（documents.py:387-390） */
export interface RagSummaryUpdateResponse {
  /** documents.py:388 */
  doc_id: string
  /** documents.py:389 */
  summary: string
}

// ── 7. 混合检索 POST /api/v1/kb/search（search.py:53）──

/** 检索请求体（models/search.py:21-34 SearchRequest）；
 *  本模块禁带 answer 键（spec「answer 字段不下发」，F2b 在线问答阻塞中），
 *  类型层不含 answer，api 层组装 body 时亦不下发 */
export interface RagSearchRequest {
  /** models/search.py:22（min_length=1, max_length=500） */
  query: string
  /** models/search.py:23（可空，pattern 限定四分类；「全部」分类不下发此字段） */
  category?: RagCategory
  /** models/search.py:24（ge=1, le=20，默认 5） */
  top_k: number
  /** models/search.py:25（默认 false；是否启用 LLM Query 改写） */
  rewrite: boolean
}

/** 命中资产预览元数据（models/search.py:5-18 AssetPreview） */
export interface RagAssetPreview {
  /** models/search.py:15（'image' | 'table'） */
  asset_type: string
  /** models/search.py:16 */
  page: number
  /** models/search.py:17（相对 KB_ASSETS_DIR 路径） */
  storage_path: string
  /** models/search.py:18（可空，默认 None） */
  mime_type: string | null
}

/** 检索结果项（models/search.py:44-72 SourceItem） */
export interface RagSearchResultItem {
  /** models/search.py:45 */
  doc_id: string
  /** models/search.py:46 */
  title: string
  /** models/search.py:47 */
  chunk_index: number
  /** models/search.py:48 */
  content: string
  /** models/search.py:49（只展示不二次计算，SSOT §17/附录 A） */
  score: number
  /** models/search.py:50 */
  rerank_score: number
  /** models/search.py:52（V2 溯源；存量 V1 文档缺省 1） */
  version: number
  /** models/search.py:53（可空；存量 V1 回填 "v1_legacy"） */
  source: string | null
  /** models/search.py:56-62（永远存在可空；KB_SUMMARY_ENABLED 关闭时恒 null） */
  summary: string | null
  /** models/search.py:66-72（缺省空数组） */
  assets: RagAssetPreview[]
}

/** 检索响应（models/search.py:75-100 SearchResponse） */
export interface RagSearchResponse {
  /** models/search.py:76（空结果为空数组，空态按 results.length===0 判定，§15.1 A3004 注） */
  results: RagSearchResultItem[]
  /** models/search.py:77（双闸门兜底降级时为 true） */
  degraded: boolean
  /** models/search.py:78（原始 query 回显） */
  query_original: string
  /** models/search.py:79（可空；降级/兜底说明原文） */
  message: string | null
  /** models/search.py:80 */
  trace_id: string
  /** models/search.py:84-90（永远存在可空；本模块 answer 恒不下发故恒 null） */
  answer: string | null
  /** models/search.py:94-99（缺省空数组；未生成回答时为空） */
  citations: string[]
}

// ── 8. 生效词典 GET /api/v1/sensitive-words/current.dict（sensitive_words.py:28-46）──

/**
 * 词典响应：text/plain 裸文本（非信封），类型即 string；
 * 走 shared/request.ts TEXT_WHITELIST 既有分支（request.ts:90/125-128），
 * 404 由拦截器映射为空串（前端据此展示空态「词典暂不可用」）。
 */
export type RagSensitiveDictResponse = string

// ── 9. 词库刷新 POST /api/v1/sensitive-words/refresh（sensitive_words.py:49-63）──

/** 刷新响应（sensitive_words.py:63）；version 为 Redis INCR 整数（sensitive_words_sync.py:116） */
export interface RagSensitiveRefreshResponse {
  /** sensitive_words.py:63 */
  version: number
}
