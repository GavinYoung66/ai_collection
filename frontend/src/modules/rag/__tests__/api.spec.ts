/**
 * rag 模块 api.ts 契约单测（f2-rag-console 批次⑥ / tasks 8.2）。
 *
 * 照 modules/qc/__tests__/api.spec.ts 先例：mock shared/request 默认导出（唯一
 * axios 实例），保留真实 TIMEOUT 常量，逐条断言 §5.0 D 表 9 端点 method+path；
 * 锁定列表分页默认值/上限钳制（>100 → 100）、上传 FormData 组装（file/category/
 * summary，空摘要不下发）、search 请求体禁带 answer 键、current.dict 走
 * responseType:'text' 白名单分支、全部端点 TIMEOUT.normal（无超时字面量）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// vi.mock 工厂会被提升，mock 函数必须经 vi.hoisted 定义
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/shared/request', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/request')>()
  return {
    ...actual,
    default: { get: mocks.get, post: mocks.post, put: mocks.put, delete: mocks.delete },
  }
})

import { TIMEOUT } from '@/shared/request'
import {
  deleteKbDocument,
  getKbDocument,
  getSensitiveWordsDict,
  listKbDocuments,
  refreshSensitiveWords,
  reindexKbDocument,
  searchKb,
  updateKbDocumentSummary,
  uploadKbDocument,
} from '../api'

const DOC_ID = 'd-00000000-0000-0000-0000-000000000001'

/** §5.0 D 表 9 端点对照表（method + path 逐条一致） */
const ENDPOINT_CASES: Array<{
  name: string
  invoke: () => Promise<unknown>
  method: 'get' | 'post' | 'put' | 'delete'
  path: string
}> = [
  {
    name: '文档列表',
    invoke: () => listKbDocuments(),
    method: 'get',
    path: '/api/v1/kb/documents',
  },
  {
    name: '上传入库',
    invoke: () => uploadKbDocument(new File(['x'], 'a.txt'), 'regulation'),
    method: 'post',
    path: '/api/v1/kb/documents',
  },
  {
    name: '文档详情',
    invoke: () => getKbDocument(DOC_ID),
    method: 'get',
    path: `/api/v1/kb/documents/${DOC_ID}`,
  },
  {
    name: '删除文档',
    invoke: () => deleteKbDocument(DOC_ID),
    method: 'delete',
    path: `/api/v1/kb/documents/${DOC_ID}`,
  },
  {
    name: '重建索引',
    invoke: () => reindexKbDocument(DOC_ID),
    method: 'post',
    path: `/api/v1/kb/documents/${DOC_ID}/reindex`,
  },
  {
    name: '摘要编辑',
    invoke: () => updateKbDocumentSummary(DOC_ID, { summary: 's' }),
    method: 'put',
    path: `/api/v1/kb/documents/${DOC_ID}/summary`,
  },
  {
    name: '混合检索',
    invoke: () => searchKb({ query: 'q', top_k: 5, rewrite: false }),
    method: 'post',
    path: '/api/v1/kb/search',
  },
  {
    name: '生效词典',
    invoke: () => getSensitiveWordsDict(),
    method: 'get',
    path: '/api/v1/sensitive-words/current.dict',
  },
  {
    name: '词库刷新',
    invoke: () => refreshSensitiveWords(),
    method: 'post',
    path: '/api/v1/sensitive-words/refresh',
  },
]

describe('modules/rag/api 契约', () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue(undefined)
    mocks.post.mockReset().mockResolvedValue(undefined)
    mocks.put.mockReset().mockResolvedValue(undefined)
    mocks.delete.mockReset().mockResolvedValue(undefined)
  })

  it.each(ENDPOINT_CASES)('端点 %s：method + path 与 D 表一致', async ({ invoke, method, path }) => {
    await invoke()
    const spy = mocks[method]
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toBe(path)
  })

  it('D 表覆盖计数：恰为 9 端点', () => {
    expect(ENDPOINT_CASES).toHaveLength(9)
  })

  it('文档列表默认参数 page=1 & page_size=20（documents.py:130-136）', async () => {
    await listKbDocuments()
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/kb/documents',
      expect.objectContaining({ params: { page: 1, page_size: 20 } }),
    )
  })

  it('文档列表 page_size 超过 100 被前端钳制为 100（documents.py:131 le=100）', async () => {
    await listKbDocuments({ page: 2, page_size: 500 })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/kb/documents',
      expect.objectContaining({ params: { page: 2, page_size: 100 } }),
    )
  })

  it('文档列表 page/page_size 低于 1 被前端钳制为 1（documents.py:130-131 ge=1）', async () => {
    await listKbDocuments({ page: 0, page_size: 0 })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/kb/documents',
      expect.objectContaining({ params: { page: 1, page_size: 1 } }),
    )
  })

  it('文档列表透传 category/status 筛选；缺省不下发', async () => {
    await listKbDocuments({ category: 'case', status: 'failed' })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/kb/documents',
      expect.objectContaining({
        params: { page: 1, page_size: 20, category: 'case', status: 'failed' },
      }),
    )
    mocks.get.mockClear()
    await listKbDocuments({})
    const params = mocks.get.mock.calls[0][1].params as Record<string, unknown>
    expect('category' in params).toBe(false)
    expect('status' in params).toBe(false)
  })

  it('上传用 FormData 组装 file/category/summary（documents.py:58 Form）', async () => {
    const file = new File(['hello'], '手册.md', { type: 'text/markdown' })
    await uploadKbDocument(file, 'regulation', '摘要')
    expect(mocks.post).toHaveBeenCalledTimes(1)
    const [path, body, config] = mocks.post.mock.calls[0]
    expect(path).toBe('/api/v1/kb/documents')
    expect(body).toBeInstanceOf(FormData)
    const form = body as FormData
    expect(form.get('file')).toBe(file)
    expect(form.get('category')).toBe('regulation')
    expect(form.get('summary')).toBe('摘要')
    expect(config).toEqual(expect.objectContaining({ timeout: TIMEOUT.normal }))
  })

  it('上传 summary 为空/缺省时不下发该字段（后端 Form 默认 ""）', async () => {
    await uploadKbDocument(new File(['x'], 'a.txt'), 'case')
    const form = mocks.post.mock.calls[0][1] as FormData
    expect(form.get('summary')).toBeNull()
    expect(form.get('category')).toBe('case')
  })

  it('search 请求体仅含 query/top_k/rewrite（+可选 category），禁带 answer 键', async () => {
    await searchKb({ query: '催收流程', top_k: 5, rewrite: false })
    const body = mocks.post.mock.calls[0][1] as Record<string, unknown>
    expect(body).toEqual({ query: '催收流程', top_k: 5, rewrite: false })
    expect('answer' in body).toBe(false)
    expect('category' in body).toBe(false)

    mocks.post.mockClear()
    await searchKb({ query: 'q', top_k: 3, rewrite: true, category: 'script' })
    expect(mocks.post.mock.calls[0][1]).toEqual({
      query: 'q',
      top_k: 3,
      rewrite: true,
      category: 'script',
    })
  })

  it('current.dict 走 text/plain 白名单分支（responseType:text，request.ts:90）', async () => {
    await getSensitiveWordsDict()
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/sensitive-words/current.dict',
      expect.objectContaining({ responseType: 'text', timeout: TIMEOUT.normal }),
    )
  })

  it('reindex/refresh 无请求体且 timeout === TIMEOUT.normal（§9.3 无超时字面量）', async () => {
    await reindexKbDocument(DOC_ID)
    expect(mocks.post).toHaveBeenCalledWith(
      `/api/v1/kb/documents/${DOC_ID}/reindex`,
      undefined,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
    mocks.post.mockClear()
    await refreshSensitiveWords()
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v1/sensitive-words/refresh',
      undefined,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
  })

  it('摘要编辑请求体为 {summary} 且 timeout === TIMEOUT.normal', async () => {
    await updateKbDocumentSummary(DOC_ID, { summary: '新摘要' })
    expect(mocks.put).toHaveBeenCalledWith(
      `/api/v1/kb/documents/${DOC_ID}/summary`,
      { summary: '新摘要' },
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
  })

  it('详情/删除 timeout === TIMEOUT.normal', async () => {
    await getKbDocument(DOC_ID)
    expect(mocks.get).toHaveBeenCalledWith(
      `/api/v1/kb/documents/${DOC_ID}`,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
    await deleteKbDocument(DOC_ID)
    expect(mocks.delete).toHaveBeenCalledWith(
      `/api/v1/kb/documents/${DOC_ID}`,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
  })
})
