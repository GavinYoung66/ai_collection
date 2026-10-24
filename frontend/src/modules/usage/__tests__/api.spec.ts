/**
 * usage 模块 api.ts 契约单测（f3-usage-console 批次⑧ / tasks 8.2）。
 *
 * 照 rag/__tests__/api.spec.ts 先例：mock shared/request 默认导出（唯一 axios 实例），
 * 保留真实 TIMEOUT 常量；断言 §5.0 E 表 2 端点 method + path、时间范围 start/end 透传、
 * trace_id 参数名、page/page_size 双向钳制、全部走 TIMEOUT.normal。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/shared/request', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/request')>()
  return { ...actual, default: { get: mocks.get } }
})

import { TIMEOUT } from '@/shared/request'
import { getUsageSummary, listUsageLogs } from '../api'

const RANGE = { start: '2026-10-17T00:00:00.000Z', end: '2026-10-24T00:00:00.000Z' }

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({})
})

describe('modules/usage/api 契约', () => {
  it('summary：method+path 与 §5.0 E 表一致，时间范围透传，TIMEOUT.normal', async () => {
    await getUsageSummary(RANGE)

    expect(mocks.get).toHaveBeenCalledTimes(1)
    const [url, config] = mocks.get.mock.calls[0]
    expect(url).toBe('/api/v1/llm-usage/summary')
    expect(config.params).toEqual({ start: RANGE.start, end: RANGE.end })
    expect(config.timeout).toBe(TIMEOUT.normal)
  })

  it('logs：默认 page=1 / page_size=20，路径全前缀，TIMEOUT.normal', async () => {
    await listUsageLogs(RANGE)

    const [url, config] = mocks.get.mock.calls[0]
    expect(url).toBe('/api/v1/llm-usage/logs')
    expect(config.params).toEqual({
      start: RANGE.start,
      end: RANGE.end,
      page: 1,
      page_size: 20,
    })
    expect(config.timeout).toBe(TIMEOUT.normal)
  })

  it('logs：trace_id / status / provider 仅在传值时下发', async () => {
    await listUsageLogs({ ...RANGE, trace_id: 'tr_abc', status: 'failed' })

    const [, config] = mocks.get.mock.calls[0]
    expect(config.params.trace_id).toBe('tr_abc')
    expect(config.params.status).toBe('failed')
    expect('provider' in config.params).toBe(false)
  })

  it('logs：page 下边界钳制到 1', async () => {
    await listUsageLogs({ ...RANGE, page: 0 })

    const [, config] = mocks.get.mock.calls[0]
    expect(config.params.page).toBe(1)
  })

  it('logs：page_size 双向钳制（0 → 1，101 → 100）', async () => {
    await listUsageLogs({ ...RANGE, page_size: 0 })
    expect(mocks.get.mock.calls[0][1].params.page_size).toBe(1)

    vi.clearAllMocks()
    mocks.get.mockResolvedValue({})
    await listUsageLogs({ ...RANGE, page_size: 101 })
    expect(mocks.get.mock.calls[0][1].params.page_size).toBe(100)
  })

  it('模块内无超时字面量：两端点均只引用 TIMEOUT.normal', async () => {
    await getUsageSummary(RANGE)
    await listUsageLogs(RANGE)

    for (const call of mocks.get.mock.calls) {
      const timeout = call[1].timeout
      expect(timeout).toBe(TIMEOUT.normal)
    }
  })
})
