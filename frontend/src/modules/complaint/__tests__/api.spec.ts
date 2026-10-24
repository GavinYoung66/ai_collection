/**
 * complaint 模块 api.ts 契约单测（tasks 1.3）。
 * mock shared/request 的默认导出（唯一 axios 实例），保留真实 TIMEOUT 常量，
 * 逐条断言 16 端点 method+path 与 §5.0 B 表一致；锁定分页默认值/上限钳制、
 * judge 独立超时与终态/等待人工枚举集合（D6：枚举与后端漂移时此处先红）。
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
  listComplaints,
  createComplaint,
  seedDemoComplaints,
  listComplaintEvents,
  getComplaint,
  judgeComplaint,
  getComplaintStatus,
  getComplaintVerdict,
  getComplaintAgentLogs,
  getComplaintTrace,
  submitDispositionVerdict,
  submitDispositionCallback,
  reviewComplaint,
  closeComplaint,
  getComplaintStats,
  switchComplaintMode,
} from '../api'
import { TERMINAL_TASK_STATUS, WAITING_HUMAN_TASK_STATUS } from '../types'

const ID = 'c-00000000-0000-0000-0000-000000000001'

/** §5.0 B 表 16 端点对照表（method + path 逐条一致） */
const ENDPOINT_CASES: Array<{
  name: string
  invoke: () => Promise<unknown>
  method: 'get' | 'post' | 'put'
  path: string
}> = [
  { name: '投诉单列表', invoke: () => listComplaints(), method: 'get', path: '/api/v1/complaints' },
  {
    name: '手工建单',
    invoke: () => createComplaint({ complaint_text: '投诉正文' }),
    method: 'post',
    path: '/api/v1/complaints',
  },
  {
    name: '演示灌数',
    invoke: () => seedDemoComplaints(),
    method: 'post',
    path: '/api/v1/complaints/demo/seed',
  },
  {
    name: '事件列表',
    invoke: () => listComplaintEvents(),
    method: 'get',
    path: '/api/v1/complaints/events',
  },
  {
    name: '投诉详情',
    invoke: () => getComplaint(ID),
    method: 'get',
    path: `/api/v1/complaints/${ID}`,
  },
  {
    name: '研判触发',
    invoke: () => judgeComplaint(ID),
    method: 'post',
    path: `/api/v1/complaints/${ID}/judge`,
  },
  {
    name: '研判状态',
    invoke: () => getComplaintStatus(ID),
    method: 'get',
    path: `/api/v1/complaints/${ID}/status`,
  },
  {
    name: '研判结论',
    invoke: () => getComplaintVerdict(ID),
    method: 'get',
    path: `/api/v1/complaints/${ID}/verdict`,
  },
  {
    name: 'agent 日志',
    invoke: () => getComplaintAgentLogs(ID),
    method: 'get',
    path: `/api/v1/complaints/${ID}/agent-logs`,
  },
  {
    name: '全链路 trace',
    invoke: () => getComplaintTrace(ID),
    method: 'get',
    path: `/api/v1/complaints/${ID}/trace`,
  },
  {
    name: '处置结论确认',
    invoke: () => submitDispositionVerdict(ID, { establishment: 'agent_violation' }),
    method: 'put',
    path: `/api/v1/complaints/${ID}/disposition/verdict`,
  },
  {
    name: '处置回调登记',
    invoke: () => submitDispositionCallback(ID),
    method: 'put',
    path: `/api/v1/complaints/${ID}/disposition/callback`,
  },
  {
    name: '人工复核',
    invoke: () => reviewComplaint(ID, { review_action: 'approve' }),
    method: 'put',
    path: `/api/v1/complaints/${ID}/review`,
  },
  {
    name: '关闭工单',
    invoke: () => closeComplaint(ID),
    method: 'put',
    path: `/api/v1/complaints/${ID}/close`,
  },
  {
    name: '投诉统计',
    invoke: () => getComplaintStats(),
    method: 'get',
    path: '/api/v1/complaints/stats',
  },
  {
    name: '运行模式切换',
    invoke: () => switchComplaintMode({ mode: 'multi_agent', signers: ['ops', 'tech'] }),
    method: 'put',
    path: '/api/v1/complaints/mode',
  },
]

describe('modules/complaint/api 契约', () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue(undefined)
    mocks.post.mockReset().mockResolvedValue(undefined)
    mocks.put.mockReset().mockResolvedValue(undefined)
  })

  it.each(ENDPOINT_CASES)('端点 %s：method + path 与 B 表一致', async ({ invoke, method, path }) => {
    await invoke()
    const spy = mocks[method]
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0]).toBe(path)
  })

  it('B 表覆盖计数：恰为 16 端点', () => {
    expect(ENDPOINT_CASES).toHaveLength(16)
  })

  it('列表默认参数 page=1 & page_size=20', async () => {
    await listComplaints()
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/complaints',
      expect.objectContaining({ params: { page: 1, page_size: 20 } }),
    )
  })

  it('page_size 超过 100 被钳制为 100', async () => {
    await listComplaints({ page: 3, page_size: 500 })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/complaints',
      expect.objectContaining({ params: { page: 3, page_size: 100 } }),
    )
  })

  it('列表 task_status 以重复参数透传（后端 list[TaskStatus] 契约，复核工作台集合过滤）', async () => {
    const reviewSet: Array<'pending_review' | 'pending_senior_review' | 'pending_callback'> = [
      'pending_review',
      'pending_senior_review',
      'pending_callback',
    ]
    await listComplaints({ task_status: reviewSet })
    expect(mocks.get).toHaveBeenCalledWith(
      '/api/v1/complaints',
      expect.objectContaining({
        params: { page: 1, page_size: 20, task_status: reviewSet },
        paramsSerializer: { indexes: false },
      }),
    )
  })

  it('judge 请求 timeout === TIMEOUT.judge', async () => {
    await judgeComplaint(ID)
    expect(mocks.post).toHaveBeenCalledWith(
      `/api/v1/complaints/${ID}/judge`,
      expect.anything(),
      expect.objectContaining({ timeout: TIMEOUT.judge }),
    )
  })

  it('非 judge 端点 timeout === TIMEOUT.normal（以详情为例）', async () => {
    await getComplaint(ID)
    expect(mocks.get).toHaveBeenCalledWith(
      `/api/v1/complaints/${ID}`,
      expect.objectContaining({ timeout: TIMEOUT.normal }),
    )
  })

  it('终态集合 = resolved/closed/archived（state.py:34-36）', () => {
    expect([...TERMINAL_TASK_STATUS]).toEqual(['resolved', 'closed', 'archived'])
  })

  it('等待人工集合 = pending_review/pending_senior_review/pending_callback（state.py:31-33）', () => {
    expect([...WAITING_HUMAN_TASK_STATUS]).toEqual([
      'pending_review',
      'pending_senior_review',
      'pending_callback',
    ])
  })
})
