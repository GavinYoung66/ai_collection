// mocks/ 入口（SSOT §16.1）：开发态 mock，仅 app/main.ts 在 import.meta.env.DEV 下
// 动态引入；本文件再按 VITE_USE_MOCK === 'true' 二次门控，双重门控满足
// 「DEV+VITE_USE_MOCK」，生产构建死分支 tree-shake 不进入产物。
//
// 实现说明：
// - 仓库未引入 msw 包（f2-rag-console 硬约束：零新增依赖，package.json 不动），
//   故以 axios 自定义 adapter 实现等价请求拦截——仅命中下方 handlers 的请求返回
//   假数据，其余请求透传原 adapter 走真实网络（登录/其他模块不受影响）；
//   响应仍经 request.ts 拦截器做信封解析，与真实后端路径完全一致；
// - depcruise `mocks-only-from-main` 规则禁止 mocks/ 内部再分层（仅 main.ts 可
//   import mocks/），故 handlers 数据与本装配逻辑同文件内联；
// - handlers 以 (method, 路径正则, resolver) 数据化描述，心智模型与 MSW 一致，
//   后续若引入 msw 可平移。
//
// 覆盖（f2-rag-console 批次⑥ / tasks 8.1，design D8）：rag 模块 §5.0 D 表 9 端点
// 信封响应（{code:0, message, data, trace_id}，成功码 int 0，对齐后端
// success_response；业务错误以 HTTP 400 + 信封 code 返回，对齐 rag-service
// BizException 语义）+ current.dict text/plain 裸文本（覆盖 request.ts
// TEXT_WHITELIST 分支）。
//
// 覆盖（f3-usage-console 批次⑧ / tasks 8.1）：LLM 用量 §5.0 E 表 2 端点
// （summary/logs），DRAFT 契约；`?shape=missing` 演示 by_service/resilience 缺失形态。
//
// 场景开关（开发演示用；单测不依赖本目录——depcruise 规定仅 app/main.ts 可引入）：
// - 上传：文件名含 'dup' → B0001 重名拒绝；含 'fail' → 轮询终态 failed
//   （带 ingest_error）；其余上传文档按 parse(25) → body_index(50) →
//   done(100)/active 逐次轮询推进；
// - 检索：query 含 'empty' → 空结果；含 'degraded' → degraded=true 兜底徽章；
//   含 'boom' → B0007 系统错误信封；
// - 词库刷新：每次调用 version 自增。如需演示 B0006 限流，可在 refreshDict 按
//   调用计数返回 { status: 429, body: { code:'B0006', message:'超出频率限制', ... } }
//   （拦截器会读 Retry-After 提示退避，此处从略）。
import axios, { type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import http from '@/shared/request'

// ── handler 契约 ──

/** resolver 入参：查询参数 / 已解析请求体 / 原始路径 */
interface MockCtx {
  params: Record<string, unknown>
  body: unknown
  path: string
}

/** resolver 返回：HTTP 状态 + 响应体（信封对象或 current.dict 的裸文本） */
interface MockReply {
  status: number
  body: unknown
}

interface MockHandler {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  /** 路径正则（不锚定开头，兼容 baseURL 前缀场景）；捕获组按序给 resolver */
  match: RegExp
  resolve: (ctx: MockCtx, m: RegExpMatchArray) => MockReply
}

// ── 信封构造（成功码 int 0，对齐后端 success_response）──
let traceSeq = 0
function mockTrace(): string {
  traceSeq += 1
  return `tr_mock_rag_${String(traceSeq).padStart(8, '0')}`
}
function ok(data: unknown): MockReply {
  return { status: 200, body: { code: 0, message: 'ok', data, trace_id: mockTrace() } }
}
function bizFail(code: string, message: string, status = 400): MockReply {
  return { status, body: { code, message, data: null, trace_id: mockTrace() } }
}

// ── 种子数据：列表多状态行（active / processing / failed）──
interface MockDocRow {
  id: string
  title: string
  category: string
  status: string
  chunks_count: number
  create_time: string
  ingest_stage: string | null
  ingest_progress: number | null
  ingest_error: string | null
}

const seedDocs: MockDocRow[] = [
  {
    id: 'd-active-0001',
    title: '催收合规红线手册.md',
    category: 'regulation',
    status: 'active',
    chunks_count: 12,
    create_time: '2026-10-20T02:00:00Z',
    ingest_stage: 'done',
    ingest_progress: 100,
    ingest_error: null,
  },
  {
    id: 'd-processing-0002',
    title: '分期话术 v3.txt',
    category: 'script',
    status: 'processing',
    chunks_count: 0,
    create_time: '2026-10-24T01:30:00Z',
    ingest_stage: 'body_index',
    ingest_progress: 50,
    ingest_error: null,
  },
  {
    id: 'd-failed-0003',
    title: '典型案例汇编.pdf',
    category: 'case',
    status: 'failed',
    chunks_count: 0,
    create_time: '2026-10-23T09:12:00Z',
    ingest_stage: null,
    ingest_progress: null,
    ingest_error: '正文抽取失败：PDF 页数超限',
  },
]

/** 上传后经异步入库推进的文档（doc_id → 轮询计数/是否失败/标题） */
const uploadedDocs = new Map<string, { polls: number; fail: boolean; title: string }>()
let uploadSeq = 0

/** 详情响应（含 ingest 三字段与 assets 元数据全字段，documents.py:149-190） */
function buildDetail(row: MockDocRow): Record<string, unknown> {
  return {
    id: row.id,
    title: row.title,
    category: row.category,
    content: `【mock 正文】${row.title} 的 PG 正文全文。`,
    status: row.status,
    version: 1,
    chunks_count: row.chunks_count,
    create_time: row.create_time,
    update_time: row.create_time,
    summary: row.status === 'active' ? 'mock 摘要：合规红线要点汇总' : null,
    ingest_stage: row.ingest_stage,
    ingest_progress: row.ingest_progress,
    ingest_error: row.ingest_error,
    assets:
      row.id === 'd-active-0001'
        ? [
            { asset_type: 'image', page: 3, storage_path: 'd-active-0001/p3_img0.png', mime_type: 'image/png' },
            { asset_type: 'table', page: 5, storage_path: 'd-active-0001/p5_tbl0.png', mime_type: 'image/png' },
          ]
        : [],
  }
}

/** 上传文档的轮询推进：parse(25) → body_index(50) → done(100)/active（或 failed） */
function uploadedDetail(
  docId: string,
  state: { polls: number; fail: boolean; title: string },
): MockDocRow {
  state.polls += 1
  if (state.polls >= 3) {
    return {
      id: docId,
      title: state.title,
      category: 'other',
      status: state.fail ? 'failed' : 'active',
      chunks_count: state.fail ? 0 : 8,
      create_time: '2026-10-24T03:00:00Z',
      ingest_stage: state.fail ? null : 'done',
      ingest_progress: state.fail ? null : 100,
      ingest_error: state.fail ? '模拟入库失败：正文为空' : null,
    }
  }
  return {
    id: docId,
    title: state.title,
    category: 'other',
    status: 'processing',
    chunks_count: 0,
    create_time: '2026-10-24T03:00:00Z',
    ingest_stage: state.polls === 1 ? 'parse' : 'body_index',
    ingest_progress: state.polls === 1 ? 25 : 50,
    ingest_error: null,
  }
}

/** 列表聚合：上传文档行（最新状态）+ 种子行，支持 category/status 过滤与分页 */
function listDocuments(ctx: MockCtx): MockReply {
  const category = typeof ctx.params.category === 'string' ? ctx.params.category : ''
  const status = typeof ctx.params.status === 'string' ? ctx.params.status : ''
  const page = Number(ctx.params.page) || 1
  const pageSize = Number(ctx.params.page_size) || 20

  const uploadedRows: MockDocRow[] = []
  for (const [docId, state] of uploadedDocs) {
    // 列表行展示当前快照但不推进轮询计数（推进由详情端点负责）
    const snapshot = { ...state }
    uploadedRows.unshift(uploadedDetail(docId, snapshot))
  }
  let items = [...uploadedRows, ...seedDocs]
  if (category) items = items.filter((row) => row.category === category)
  if (status) items = items.filter((row) => row.status === status)

  const total = items.length
  const start = (page - 1) * pageSize
  return ok({ items: items.slice(start, start + pageSize), total, page, page_size: pageSize })
}

function uploadDocument(ctx: MockCtx): MockReply {
  const form = ctx.body instanceof FormData ? ctx.body : null
  const file = form?.get('file')
  const title = file instanceof File ? file.name : 'mock-upload.txt'
  // 场景开关：文件名含 'dup' → B0001 重名拒绝（后端防重语义，message 直接透出）
  if (title.includes('dup')) {
    return bizFail('B0001', '同名文件正在入库中，请勿重复上传')
  }
  uploadSeq += 1
  const docId = `d-mock-${String(uploadSeq).padStart(4, '0')}`
  uploadedDocs.set(docId, { polls: 0, fail: title.includes('fail'), title })
  // 异步入库早返回（README §G BREAKING）：status 恒 processing，前端轮询终态
  return ok({ doc_id: docId, status: 'processing' })
}

function getDocument(_ctx: MockCtx, m: RegExpMatchArray): MockReply {
  const docId = m[1]
  const uploaded = uploadedDocs.get(docId)
  if (uploaded) return ok(buildDetail(uploadedDetail(docId, uploaded)))
  const row = seedDocs.find((item) => item.id === docId)
  if (!row) return bizFail('B0004', '文档不存在')
  return ok(buildDetail(row))
}

function deleteDocument(_ctx: MockCtx, m: RegExpMatchArray): MockReply {
  const docId = m[1]
  uploadedDocs.delete(docId)
  // 删除成功信封 data 为 null（documents.py:219）
  return ok(null)
}

function reindexDocument(_ctx: MockCtx, m: RegExpMatchArray): MockReply {
  // 同步重建：仅按正文重切，assets_rebuilt 恒 false（documents.py:350）
  return ok({ doc_id: m[1], chunks_count: 12, status: 'active', assets_rebuilt: false })
}

function updateSummary(ctx: MockCtx, m: RegExpMatchArray): MockReply {
  const body = (ctx.body ?? {}) as { summary?: string }
  return ok({ doc_id: m[1], summary: body.summary ?? '' })
}

function searchKb(ctx: MockCtx): MockReply {
  const body = (ctx.body ?? {}) as { query?: string }
  const query = body.query ?? ''
  // 场景开关：'boom' → B0007 兜底；'empty' → 空结果；'degraded' → 双闸门兜底降级
  if (query.includes('boom')) return bizFail('B0007', '系统内部错误')
  const degraded = query.includes('degraded')
  const results = query.includes('empty')
    ? []
    : [
        {
          doc_id: 'd-active-0001',
          title: '催收合规红线手册.md',
          chunk_index: 2,
          content: '禁止向无关第三方泄露债务人隐私信息……',
          score: 0.83,
          rerank_score: 0.91,
          version: 2,
          source: 'v2_ingest',
          summary: 'mock 摘要：合规红线要点汇总',
          assets: [],
        },
        {
          doc_id: 'd-active-0001',
          title: '催收合规红线手册.md',
          chunk_index: 5,
          content: '客户要求停止通话须立即停止……',
          score: 0.77,
          rerank_score: 0.85,
          version: 2,
          source: 'v2_ingest',
          summary: null,
          assets: [],
        },
      ]
  return ok({
    results,
    degraded,
    query_original: query,
    message: degraded ? 'Embedding 服务不可用，已切换 BM25 兜底召回' : null,
    trace_id: mockTrace(),
    answer: null,
    citations: [],
  })
}

/** 生效词典：text/plain 裸文本（非信封；覆盖 request.ts TEXT_WHITELIST 分支） */
function currentDict(): MockReply {
  return { status: 200, body: '暴力催收\n上门威胁\n泄露隐私\n冒充公检法\n' }
}

let dictVersion = 3
function refreshDict(): MockReply {
  dictVersion += 1
  return ok({ version: dictVersion })
}

// ── LLM 用量（f3-usage-console 批次⑧ / tasks 8.1，design D1/D8）──
// DRAFT 契约：后端 2 端点未实现，此处仅用于前端行为验收，字段锚定
// common/db/models/llm_log.py。双形态：
// - 默认完整形态：含 by_service + resilience；
// - `?shape=missing`：两者缺失，用于验收「整块隐藏」。
// status 使用实际写入值（ok / qc_review_exemption / skipped）与原注释四值混排，
// 验证前端不做映射、原样透出。
// 数据按请求窗口 start/end 生成：趋势分桶、totals、明细时间随 1d/7d/30d 切换变化；
// 模型统一为官方调用名 deepseek-flash（= DeepSeek-V4.1-Flash，api-docs.deepseek.com）；
// 真实后端 qc-service 当前仍 qwen-plus（见 qc-service config.py，切换需用户确认）。

/** 请求窗口解析：读 start/end（ISO 8601），缺省回退近 7 天 */
function resolveUsageWindow(ctx: MockCtx): { startMs: number; endMs: number } {
  const start = typeof ctx.params.start === 'string' ? Date.parse(ctx.params.start) : NaN
  const end = typeof ctx.params.end === 'string' ? Date.parse(ctx.params.end) : NaN
  const endMs = Number.isFinite(end) ? end : Date.now()
  const startMs = Number.isFinite(start) ? start : endMs - 7 * 24 * 60 * 60 * 1000
  return { startMs: Math.min(startMs, endMs), endMs }
}

/** 趋势分桶：跨度 ≤1 天按小时、否则按天（上限 62 桶）；桶序号做确定性种子，同窗口数据稳定 */
function mockUsageTrend(
  startMs: number,
  endMs: number,
): Array<{ bucket_start: string; calls: number; failure_rate: number }> {
  const HOUR = 60 * 60 * 1000
  const bucketMs = endMs - startMs <= 24 * HOUR ? HOUR : 24 * HOUR
  const count = Math.min(Math.ceil((endMs - startMs) / bucketMs), 62)
  const rows = []
  for (let i = 0; i < count; i += 1) {
    const bucketStart = startMs + i * bucketMs
    const seed = Math.floor(bucketStart / bucketMs)
    rows.push({
      bucket_start: new Date(bucketStart).toISOString(),
      calls: 8 + ((seed * 7) % 17) + Math.round((i / Math.max(count - 1, 1)) * 12),
      failure_rate: Number((0.018 + ((seed * 13) % 9) * 0.009).toFixed(4)),
    })
  }
  return rows
}

/** 明细种子：24 行均布在窗口内（行号固定保证 trace_id 检索稳定），status 轮换覆盖实际写入值与原注释值 */
function mockUsageLogs(startMs: number, endMs: number): Array<Record<string, unknown>> {
  const statuses = ['ok', 'failed', 'qc_review_exemption', 'skipped', 'timeout', 'circuit_open']
  const span = Math.max(endMs - startMs, 60 * 1000)
  return Array.from({ length: 24 }, (_, i) => ({
    id: `mock-usage-${String(i + 1).padStart(4, '0')}`,
    trace_id: `tr_mock_usage_${String(i + 1).padStart(8, '0')}`,
    session_id: i % 3 === 0 ? null : `sess-${1000 + i}`,
    provider: 'deepseek',
    model: 'deepseek-flash',
    token_usage: { prompt_tokens: 320 + i, completion_tokens: 120 + i, total_tokens: 440 + i * 2 },
    latency_ms: i % 7 === 0 ? null : 620 + i * 13,
    confidence: i % 5 === 0 ? null : 0.9,
    status: statuses[i % statuses.length],
    error_msg: i % 6 === 1 ? `mock error: upstream returned 500 (row ${i + 1})` : null,
    // 最新一行贴 end、往回按窗口均布：切窗口后明细时间随之变化
    create_time: new Date(endMs - (span * i) / 24).toISOString(),
  }))
}

function usageSummary(ctx: MockCtx): MockReply {
  const missing = ctx.params.shape === 'missing'
  const { startMs, endMs } = resolveUsageWindow(ctx)
  const trend = mockUsageTrend(startMs, endMs)
  const calls = trend.reduce((sum, bucket) => sum + bucket.calls, 0)
  const failureCalls = trend.reduce((sum, bucket) => sum + bucket.calls * bucket.failure_rate, 0)
  const data: Record<string, unknown> = {
    totals: {
      calls,
      tokens: calls * 753,
      avg_latency_ms: Number((812.4 - (trend.length % 5) * 13.7).toFixed(1)),
      failure_rate: calls ? Number((failureCalls / calls).toFixed(4)) : 0,
    },
    by_model: [{ model: 'deepseek-flash', calls, tokens: calls * 753 }],
    trend,
  }
  if (!missing) {
    data.by_service = [{ service: 'qc', calls }]
    data.resilience = {
      rate_limited: Math.round(calls * 0.023),
      breaker_open: Math.round(calls * 0.008),
      fallback: Math.round(calls * 0.016),
    }
  }
  return ok(data)
}

function usageLogs(ctx: MockCtx): MockReply {
  const page = Math.max(Number(ctx.params.page ?? 1) || 1, 1)
  const pageSizeRaw = Number(ctx.params.page_size ?? 20) || 20
  const pageSize = Math.min(Math.max(pageSizeRaw, 1), 100)
  const statusFilter = typeof ctx.params.status === 'string' ? ctx.params.status : ''
  const traceFilter = typeof ctx.params.trace_id === 'string' ? ctx.params.trace_id : ''
  const { startMs, endMs } = resolveUsageWindow(ctx)

  let rows = mockUsageLogs(startMs, endMs)
  if (statusFilter) rows = rows.filter((row) => row.status === statusFilter)
  if (traceFilter) rows = rows.filter((row) => row.trace_id === traceFilter)

  const start = (page - 1) * pageSize
  return ok({
    items: rows.slice(start, start + pageSize),
    total: rows.length,
    page,
    page_size: pageSize,
  })
}

/**
 * rag 9 端点 + current.dict 文本响应（顺序敏感：reindex/summary 必须先于详情匹配）
 * + LLM 用量 2 端点（路径独立，无冲突）。
 */
const handlers: MockHandler[] = [
  { method: 'GET', match: /\/api\/v1\/llm-usage\/summary$/, resolve: usageSummary },
  { method: 'GET', match: /\/api\/v1\/llm-usage\/logs$/, resolve: usageLogs },
  { method: 'POST', match: /\/api\/v1\/kb\/documents\/([^/]+)\/reindex$/, resolve: reindexDocument },
  { method: 'PUT', match: /\/api\/v1\/kb\/documents\/([^/]+)\/summary$/, resolve: updateSummary },
  { method: 'GET', match: /\/api\/v1\/kb\/documents\/([^/]+)$/, resolve: getDocument },
  { method: 'DELETE', match: /\/api\/v1\/kb\/documents\/([^/]+)$/, resolve: deleteDocument },
  { method: 'GET', match: /\/api\/v1\/kb\/documents$/, resolve: listDocuments },
  { method: 'POST', match: /\/api\/v1\/kb\/documents$/, resolve: uploadDocument },
  { method: 'POST', match: /\/api\/v1\/kb\/search$/, resolve: searchKb },
  { method: 'GET', match: /\/api\/v1\/sensitive-words\/current\.dict$/, resolve: currentDict },
  { method: 'POST', match: /\/api\/v1\/sensitive-words\/refresh$/, resolve: refreshDict },
]

// ── 装配：axios 自定义 adapter 拦截（VITE_USE_MOCK 门控）──

/** 拦截安装幂等标记（setupMocks 可能被重复调用） */
let installed = false

/** 请求体解析：axios transformRequest 已在 adapter 前运行——JSON 已序列化为
 *  字符串（需 parse 回对象），FormData 原样保留（上传端点读取 file/category） */
function parseBody(data: unknown): unknown {
  if (typeof data !== 'string') return data
  try {
    return JSON.parse(data) as unknown
  } catch {
    return data
  }
}

function toAxiosResponse(
  config: InternalAxiosRequestConfig,
  reply: MockReply,
): AxiosResponse {
  return {
    data: reply.body,
    status: reply.status,
    statusText: reply.status === 200 ? 'OK' : 'ERROR',
    // 拦截器经 headers['x-trace-id'] 读取 trace，缺省回退信封 trace_id，此处无需注入
    headers: {},
    config,
  }
}

export async function setupMocks(): Promise<void> {
  if (installed) return
  // 第二道门控：仅显式开启时安装（第一道 DEV 在 app/main.ts）
  if (import.meta.env.VITE_USE_MOCK !== 'true') return
  installed = true

  const fallback = axios.getAdapter(http.defaults.adapter)
  http.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const url = config.url ?? ''
    const method = (config.method ?? 'get').toUpperCase()
    for (const handler of handlers) {
      if (handler.method !== method) continue
      const m = url.match(handler.match)
      if (!m) continue
      const reply = handler.resolve(
        {
          params: (config.params ?? {}) as Record<string, unknown>,
          body: parseBody(config.data),
          path: url,
        },
        m,
      )
      return toAxiosResponse(config, reply)
    }
    // 未命中 mock 端点：透传真实网络（mock 仅覆盖 rag + LLM 用量）
    return fallback(config)
  }
}
