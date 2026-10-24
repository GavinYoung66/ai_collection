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
interface RagMockCtx {
  params: Record<string, unknown>
  body: unknown
  path: string
}

/** resolver 返回：HTTP 状态 + 响应体（信封对象或 current.dict 的裸文本） */
interface RagMockReply {
  status: number
  body: unknown
}

interface RagMockHandler {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  /** 路径正则（不锚定开头，兼容 baseURL 前缀场景）；捕获组按序给 resolver */
  match: RegExp
  resolve: (ctx: RagMockCtx, m: RegExpMatchArray) => RagMockReply
}

// ── 信封构造（成功码 int 0，对齐后端 success_response）──
let traceSeq = 0
function mockTrace(): string {
  traceSeq += 1
  return `tr_mock_rag_${String(traceSeq).padStart(8, '0')}`
}
function ok(data: unknown): RagMockReply {
  return { status: 200, body: { code: 0, message: 'ok', data, trace_id: mockTrace() } }
}
function bizFail(code: string, message: string, status = 400): RagMockReply {
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
function listDocuments(ctx: RagMockCtx): RagMockReply {
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

function uploadDocument(ctx: RagMockCtx): RagMockReply {
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

function getDocument(_ctx: RagMockCtx, m: RegExpMatchArray): RagMockReply {
  const docId = m[1]
  const uploaded = uploadedDocs.get(docId)
  if (uploaded) return ok(buildDetail(uploadedDetail(docId, uploaded)))
  const row = seedDocs.find((item) => item.id === docId)
  if (!row) return bizFail('B0004', '文档不存在')
  return ok(buildDetail(row))
}

function deleteDocument(_ctx: RagMockCtx, m: RegExpMatchArray): RagMockReply {
  const docId = m[1]
  uploadedDocs.delete(docId)
  // 删除成功信封 data 为 null（documents.py:219）
  return ok(null)
}

function reindexDocument(_ctx: RagMockCtx, m: RegExpMatchArray): RagMockReply {
  // 同步重建：仅按正文重切，assets_rebuilt 恒 false（documents.py:350）
  return ok({ doc_id: m[1], chunks_count: 12, status: 'active', assets_rebuilt: false })
}

function updateSummary(ctx: RagMockCtx, m: RegExpMatchArray): RagMockReply {
  const body = (ctx.body ?? {}) as { summary?: string }
  return ok({ doc_id: m[1], summary: body.summary ?? '' })
}

function searchKb(ctx: RagMockCtx): RagMockReply {
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
function currentDict(): RagMockReply {
  return { status: 200, body: '暴力催收\n上门威胁\n泄露隐私\n冒充公检法\n' }
}

let dictVersion = 3
function refreshDict(): RagMockReply {
  dictVersion += 1
  return ok({ version: dictVersion })
}

/**
 * rag 9 端点 + current.dict 文本响应（顺序敏感：reindex/summary 必须先于详情匹配）。
 */
const ragHandlers: RagMockHandler[] = [
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
  reply: RagMockReply,
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
    for (const handler of ragHandlers) {
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
    // 未命中 rag 端点：透传真实网络（mock 仅覆盖 rag 模块）
    return fallback(config)
  }
}
