/**
 * qc 模块 api.ts 契约单测（tasks 1.5）。
 * mock shared/request 的默认导出（唯一 axios 实例），保留真实 TIMEOUT 常量，
 * 逐条断言 15 端点 method+path 与 §5.0 C 表一致；锁定分页默认值/上限钳制、
 * replay/{session_id}、replay/raw 与 check/batch 的 qcReplay 长超时、replay/raw 幂等头、
 * 其余端点 TIMEOUT.normal（design D1/D6：契约漂移时此处先红）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// vi.mock 工厂会被提升，mock 函数必须经 vi.hoisted 定义
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/shared/request', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/request')>()
  return {
    ...actual,
    default: { get: mocks.get, post: mocks.post, put: mocks.put },
  }
})

import { TIMEOUT } from '@/shared/request'
import {
  seedQcDemo,
  listQcDemoPending,
  getQcDemoTrace,
  listQcRecords,
  getQcRecordStats,
  getQcRecord,
  replayQcRaw,
  replayQcSession,
  checkQcBatch,
  listQcReviewsPending,
  submitQcReview,
  listQcAlerts,
  ackQcAlert,
  getQcRulesActive,
  applyQcRules,
} from '../api'
import type { QcReplayRawRequest } from '../types'

const SESSION_ID = 'qc_demo_00000000000000000000000000000001'
const QC_RECORD_ID = 'r-00000000-0000-0000-0000-000000000001'
const ALERT_ID = 'a-00000000-0000-0000-0000-000000000001'

/** replay/raw 最小合法请求体（check.py:60-77：call_ended 恒 true、turns 至少 1 条） */
const RAW_BODY: QcReplayRawRequest = {
  session_id: SESSION_ID,
  case_id: 'c-00000000-0000-0000-0000-000000000001',
  call_ended: true,
  turns: [{ turn_id: 't1', speaker: 'agent', text: '您好', turn_index: 0 }],
}

/** §5.0 C 表 15 端点对照表（method + path 逐条一致） */
const ENDPOINT_CASES: Array<{
  name: string
  invoke: () => Promise<unknown>
  method: 'get' | 'post'
  path: string
}> = [
  { name: '灌入演示数据', invoke: () => seedQcDemo(), method: 'post', path: '/api/v1/qc/demo/seed' },
  {
    name: '待检会话队列',
    invoke: () => listQcDemoPending(),
    method: 'get',
    path: '/api/v1/qc/demo/pending',
  },
  {
    name: '演示全链路 trace',
    invoke: () => getQcDemoTrace(SESSION_ID),
    method: 'get',
    path: `/api/v1/qc/demo/${SESSION_ID}/trace`,
  },
  {
    name: '质检记录列表',
    invoke: () => listQcRecords(),
    method: 'get',
    path: '/api/v1/qc/records',
  },
  {
    name: '质检记录统计',
    invoke: () => getQcRecordStats({ group_by: 'rule_id' }),
    method: 'get',
    path: '/api/v1/qc/records/stats',
  },
  {
    name: '质检记录详情',
    invoke: () => getQcRecord(SESSION_ID),
    method: 'get',
    path: `/api/v1/qc/records/${SESSION_ID}`,
  },
  {
    name: '会话回放（原始）',
    invoke: () => replayQcRaw(RAW_BODY),
    method: 'post',
    path: '/api/v1/qc/replay/raw',
  },
  {
    name: '会话质检触发/回放',
    invoke: () => replayQcSession(SESSION_ID),
    method: 'post',
    path: `/api/v1/qc/replay/${SESSION_ID}`,
  },
  {
    name: '批量质检',
    invoke: () => checkQcBatch({ batch_id: 'b-1', session_ids: [SESSION_ID] }),
    method: 'post',
    path: '/api/v1/qc/check/batch',
  },
  {
    name: '待复核队列',
    invoke: () => listQcReviewsPending(),
    method: 'get',
    path: '/api/v1/qc/reviews/pending',
  },
  {
    name: '提交复核',
    invoke: () => submitQcReview(QC_RECORD_ID, { action: 'CONFIRM' }),
    method: 'post',
    path: `/api/v1/qc/reviews/${QC_RECORD_ID}`,
  },
  {
    name: '告警列表',
    invoke: () => listQcAlerts(),
    method: 'get',
    path: '/api/v1/qc/alerts',
  },
  {
    name: '告警确认',
    invoke: () => ackQcAlert(ALERT_ID),
    method: 'post',
    path: `/api/v1/qc/alerts/${ALERT_ID}/ack`,
  },
  {
    name: '生效规则列表',
    invoke: () => getQcRulesActive(),
    method: 'get',
    path: '/api/v1/qc/rules/active',
  },
  {
    name: '规则热应用',
    invoke: () => applyQcRules({ version: 'v1.0' }),
    method: 'post',
    path: '/api/v1/qc/rules/apply',
  },
]

describe('modules/qc/api 契约', () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue(undefined)
    mocks.post.mockReset().mockResolvedValue(undefined)
    mocks.put.mockReset().mockResolvedValue(undefined)
  })

  it.each(ENDPOINT_CASES)('端点 %s：method + path 与 C 表一致', async ({ invoke, method, path }) => {
    await invoke()
    const spy = mocks[method]
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toBe(path)
  })

  it('C 表覆盖计数：恰为 15 端点', () => {
    expect(ENDPOINT_CASES).toHaveLength(15)
  })

  it('records 列表默认参数 page=1 & page_size=20（records.py:116-117）', async () => {
    await listQcRecords()
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/qc/records',
      expect.objectContaining({ params: { page: 1, page_size: 20 } }),
    )
  })

  it('records 列表 page_size 超过 100 被钳制为 100（records.py:128-129 后端 A2002 前置拦截）', async () => {
    await listQcRecords({ page: 3, page_size: 500 })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/qc/records',
      expect.objectContaining({ params: { page: 3, page_size: 100 } }),
    )
  })

  it('stats 透传 group_by 与 from/to 查询别名（records.py:93-95）', async () => {
    await getQcRecordStats({ group_by: 'day', from: '2026-10-01T00:00:00Z', to: '2026-10-23T00:00:00Z' })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/qc/records/stats',
      expect.objectContaining({
        params: { group_by: 'day', from: '2026-10-01T00:00:00Z', to: '2026-10-23T00:00:00Z' },
      }),
    )
  })

  it('replay/{session_id} 请求 timeout === TIMEOUT.qcReplay', async () => {
    await replayQcSession(SESSION_ID)
    expect(mocks.post).toHaveBeenCalledWith(
      `/api/v1/qc/replay/${SESSION_ID}`,
      expect.anything(),
      expect.objectContaining({ timeout: TIMEOUT.qcReplay }),
    )
  })

  it('check/batch 请求 timeout === TIMEOUT.qcReplay', async () => {
    await checkQcBatch({ batch_id: 'b-1', session_ids: [SESSION_ID] })
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v1/qc/check/batch',
      expect.anything(),
      expect.objectContaining({ timeout: TIMEOUT.qcReplay }),
    )
  })

  it('replay/raw 请求头携带 X-Idempotency-Key（UUID，check.py:163 必填头）', async () => {
    await replayQcRaw(RAW_BODY)
    const config = mocks.post.mock.calls[0][2] as { headers: Record<string, string> }
    expect(config.headers['X-Idempotency-Key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    )
  })

  it('replay/raw 每次调用生成新的 X-Idempotency-Key', async () => {
    await replayQcRaw(RAW_BODY)
    await replayQcRaw(RAW_BODY)
    const first = (mocks.post.mock.calls[0][2] as { headers: Record<string, string> }).headers[
      'X-Idempotency-Key'
    ]
    const second = (mocks.post.mock.calls[1][2] as { headers: Record<string, string> }).headers[
      'X-Idempotency-Key'
    ]
    expect(first).not.toBe(second)
  })

  it('replay/raw timeout === TIMEOUT.qcReplay（同步含 LLM 复核，SSOT §5.0 C 表 L218 / §9.3）', async () => {
    await replayQcRaw(RAW_BODY)
    expect(mocks.post).toHaveBeenCalledWith(
      '/api/v1/qc/replay/raw',
      expect.anything(),
      expect.objectContaining({ timeout: TIMEOUT.qcReplay }),
    )
  })

  it('force 重跑经 X-QC-Force-Rerun 头透传（check.py:139-140）', async () => {
    await replayQcSession(SESSION_ID, {}, { forceRerun: true })
    expect(mocks.post).toHaveBeenCalledWith(
      `/api/v1/qc/replay/${SESSION_ID}`,
      expect.anything(),
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-QC-Force-Rerun': 'true' }),
      }),
    )
  })

  it('非长请求端点 timeout === TIMEOUT.normal（以详情为例）', async () => {
    await getQcRecord(SESSION_ID)
    expect(mocks.get).toHaveBeenCalledWith(
      `/api/v1/qc/records/${SESSION_ID}`,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
  })
})
