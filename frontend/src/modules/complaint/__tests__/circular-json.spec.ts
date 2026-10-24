/**
 * circular-json.spec.ts —— 复现 browser_use 报告的 PageTable.vue circular JSON 错误。
 *
 * 背景：browser_use 在 http://localhost:5173/admin/ 硬刷新后看到：
 *   TypeError: Converting circular structure to JSON
 *     at JSON.stringify
 *     at toDisplayString (vue.runtime.esm-bundler.js)
 *     at PageTable.vue:253  ← 编译后行号，对应 template 里的某个 {{ }} 插值
 *     ...renderList...
 *
 * 本测试不 stub PageTable（让它真的被渲染），用 mock 数据模拟 ComplaintList.vue 的真实场景。
 * 如果这个测试也抛错 → 问题在前端代码；如果不抛错 → 问题在 vite dev server 特有因素。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import ComplaintList from '../views/ComplaintList.vue'
import { PageTable } from '@/shared'

const mockRouter = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/complaints', component: ComplaintList },
    { path: '/complaints/review', component: ComplaintList },
    { path: '/complaints/:id', component: ComplaintList },
  ],
})

vi.mock('../api', () => ({
  listComplaints: vi.fn().mockResolvedValue({
    items: [
      {
        complaint_id: 'cp-001',
        complaint_text: '用户投诉物流配送延迟严重，超过预定时间三天还没收到货，联系客服也没人响应',
        task_status: 'pending',
        verdict: null,
        created_at: '2025-01-15 10:30:00',
        mode: 'ai_first',
        review_needed: true,
        review_status: 'pending',
      },
      {
        complaint_id: 'cp-002',
        complaint_text: '客服态度极其恶劣，不仅不解决问题还对用户进行人身攻击',
        task_status: 'completed',
        verdict: 'genuine',
        created_at: '2025-01-14 15:20:00',
        mode: 'ai_first',
        review_needed: false,
        review_status: 'completed',
      },
    ],
    total: 2,
    page: 1,
    page_size: 20,
  }),
  getComplaintStats: vi.fn().mockResolvedValue({
    total: 2,
    by_status: { pending: 1, processing: 0, completed: 1 },
    by_verdict: { genuine: 1, false_alarm: 0, needs_review: 0 },
    avg_processing_minutes: 45.2,
    evidence_insufficient_rate: 0.15,
  }),
  createComplaint: vi.fn().mockResolvedValue({ complaint_id: 'cp-new' }),
  seedDemoComplaints: vi.fn().mockResolvedValue({ seeded: 10 }),
  listComplaintEvents: vi.fn().mockResolvedValue([]),
  switchComplaintMode: vi.fn().mockResolvedValue({ mode: 'manual', gray_percent: 0 }),
}))

// 防止 JSON.stringify 循环引用错误被静默吞掉——显式捕获并报告
describe('circular JSON error reproduction', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('render PageTable directly with columns that have render() returning primitives', () => {
    const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const wrapper = mount(PageTable, {
      props: {
        columns: [
          { key: 'id', label: 'ID', width: 100 },
          { key: 'name', label: '名称', render: (row: unknown) => (row as { name: string }).name.slice(0, 10) + '…' },
          { key: 'status', label: '状态', width: 100 },
        ],
        rows: [
          { id: '1', name: '测试名称一', status: 'ok' },
          { id: '2', name: '测试名称二更长一些', status: 'pending' },
        ],
        total: 2,
        page: 1,
        pageSize: 20,
      },
    })

    // 不应该有 console.error 或 console.warn（Vue 不会 silent fail）
    const errors = consoleErrorSpy.mock.calls.filter(
      (c) => Array.isArray(c) && c.some((arg) => typeof arg === 'string' && arg.includes('circular'))
    )
    expect(errors).toHaveLength(0)
    expect(wrapper.exists()).toBe(true)

    consoleWarnSpy.mockRestore()
    consoleErrorSpy.mockRestore()
  })

  it('render ComplaintList (does NOT stub PageTable — real render path)', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    // 关键：不用 stubs —— PageTable 真的被渲染
    const wrapper = mount(ComplaintList, {
      global: {
        plugins: [mockRouter],
      },
    })

    // 等 onMounted 里的 fetch 完成
    await vi.waitFor(() => {
      return wrapper.text().includes('cp-001') || consoleErrorSpy.mock.calls.length > 0
    }, { timeout: 5000 })

    // 检查有没有 circular 相关的 console.error
    const circularErrors = consoleErrorSpy.mock.calls.filter(
      (c) => Array.isArray(c) && c.some((arg) =>
        (typeof arg === 'string' && arg.includes('circular')) ||
        (typeof arg === 'object' && arg !== null && 'message' in arg && typeof (arg as { message: string }).message === 'string' && (arg as { message: string }).message.includes('circular'))
      )
    )

    expect(circularErrors).toHaveLength(0)

    consoleErrorSpy.mockRestore()
  })
})
