/**
 * ComplaintDetail 交互单测（tasks 3.4）。
 *
 * 覆盖：
 * ① judge 防连点（连点 3 次仅 1 次请求）
 * ② 改判原因空拦截（不一致时空原因被拦截）
 * ③ 关单失败提示含 trace 后 8 位
 * ④ A5001 / A5002 / B0005 / B0010 / A5015-17 模块侧动作
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { getTraceContext, setTraceContext } from '@/shared/request'

// ── Mocks ──
const apiMocks = vi.hoisted(() => ({
  getComplaint: vi.fn(),
  getComplaintVerdict: vi.fn(),
  getComplaintTrace: vi.fn(),
  getComplaintAgentLogs: vi.fn(),
  judgeComplaint: vi.fn(),
  submitDispositionVerdict: vi.fn(),
  submitDispositionCallback: vi.fn(),
  reviewComplaint: vi.fn(),
  closeComplaint: vi.fn(),
}))

vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: {
    success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
  },
  ElMessageBox: { alert: vi.fn().mockResolvedValue(undefined) },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

const routerPush = vi.hoisted(() => vi.fn())
const routeMocks = vi.hoisted(() => ({ query: {} as Record<string, unknown> }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/complaints/c-001', params: { id: 'c-001' }, query: routeMocks.query }),
}))

const pollingMocks = vi.hoisted(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  isPolling: { value: true }, // mount 后视为轮询已启动
  isStopped: { value: false },
  status: { value: 'judging' as const },
  error: { value: null as string | null },
  errorTrace: { value: null as string | null },
  pollNow: vi.fn(),
}))
vi.mock('../composables/usePolling', () => ({
  usePolling: () => pollingMocks,
}))

import ComplaintDetail from '../views/ComplaintDetail.vue'

// EP stubs
function stubEP() {
  return {
    'el-skeleton': true, 'el-empty': {
      template: '<div class="ep-empty">{{ description }}<slot /></div>',
      props: ['description', 'imageSize'],
    }, 'el-alert': true,
    'el-steps': { template: '<div><slot /></div>' }, 'el-step': true,
    'el-tag': { template: '<span><slot /></span>' },
    'el-descriptions': { template: '<div><slot /></div>' },
    'el-descriptions-item': { template: '<div><slot /></div>' },
    'el-button': {
      template: '<button class="ep-button" :disabled="disabled || loading"><slot /></button>',
      props: ['disabled', 'loading', 'type'],
    },
    'el-tabs': { template: '<div><slot /></div>' }, 'el-tab-pane': true,
    'el-divider': true,
    'el-form': { template: '<form><slot /></form>' },
    'el-form-item': { template: '<div><slot /></div>' },
    'el-select': { template: '<select><slot /></select>' }, 'el-option': true,
    'el-input': true, 'el-table': true, 'el-timeline': true, 'el-timeline-item': true,
  }
}
function mountDetail() { return mount(ComplaintDetail, { global: { stubs: stubEP() } }) }

const mockDetail = {
  complaint_id: 'c-001', complaint_text: '投诉内容', task_status: 'pending_review',
  verdict: null, ai_verdict: 'agent_violation', disposition_verdict: null,
  disposition_reason: null, callback_scheduled: false, ordered_evidence: [],
  evidence_refs: [], confidence: null, response_draft: null,
  created_at: '2026-10-23T10:00:00Z', updated_at: '2026-10-23T10:00:00Z',
}

function mockOK() {
  apiMocks.getComplaint.mockResolvedValue(mockDetail)
  apiMocks.getComplaintVerdict.mockResolvedValue({ complaint_id: 'c-001', verdict: null, confidence: null, response_draft: null, regulation_refs: [], rag_source_ids: [], evidence_refs: [] })
}

/** 指定 task_status 的详情（操作区按状态显隐后，需按目标操作切换到可达状态） */
function mockStatus(status: string) {
  apiMocks.getComplaint.mockResolvedValue({ ...mockDetail, task_status: status })
  apiMocks.getComplaintVerdict.mockResolvedValue({ complaint_id: 'c-001', verdict: null, confidence: null, response_draft: null, regulation_refs: [], rag_source_ids: [], evidence_refs: [] })
}

/** 找按钮 */
function findButton(wrapper: ReturnType<typeof mountDetail>, text: string) {
  const btn = wrapper.findAll('.ep-button').find(b => b.text().includes(text))
  expect(btn, `应找到「${text}」按钮`).toBeTruthy()
  return btn!
}

describe('modules/complaint/ComplaintDetail 交互', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 重建 ElMessage 子方法（vi.clearAllMocks 只清调用，不重置 vi.fn() 本体）
    epMocks.ElMessage.success = vi.fn()
    epMocks.ElMessage.error = vi.fn()
    epMocks.ElMessage.warning = vi.fn()
    epMocks.ElMessage.info = vi.fn()
    epMocks.ElMessageBox.alert = vi.fn().mockResolvedValue(undefined)
    mockOK()
  })

  it('1. judge 防连点：连点 3 次仅 1 次请求', async () => {
    apiMocks.judgeComplaint.mockResolvedValue({ complaint_id: 'c-001', task_status: 'judging', verdict: null, thread_id: null, message: 'ok' })
    const wrapper = mountDetail(); await flushPromises()

    const btn = findButton(wrapper, '触发研判')
    await btn.trigger('click')
    await btn.trigger('click')
    await btn.trigger('click')
    await flushPromises()

    expect(apiMocks.judgeComplaint).toHaveBeenCalledTimes(1)
  })

  it('2. 改判原因空拦截：与 AI 初选不一致时原因空被拦截', async () => {
    apiMocks.submitDispositionVerdict.mockResolvedValue({ complaint_id: 'c-001', task_status: 'pending_review', establishment: 'user_misunderstanding', ai_verdict: 'agent_violation' })
    const wrapper = mountDetail(); await flushPromises()

    await findButton(wrapper, '提交定责').trigger('click')
    await flushPromises()

    expect(apiMocks.submitDispositionVerdict).not.toHaveBeenCalled()
  })

  it('3. 关单失败 → ElMessage 提示含 trace 后 8 位', async () => {
    mockStatus('resolved') // 关单仅在 resolved 可达
    apiMocks.closeComplaint.mockRejectedValue({ message: '前置校验失败', code: 'A5002', trace: 'tr_0123456789abcdef' })
    const wrapper = mountDetail(); await flushPromises()

    await findButton(wrapper, '关单').trigger('click')
    await flushPromises()

    // ElMessage 任一子方法被调且含 trace 后 8 位
    const allCalls = [
      ...epMocks.ElMessage.success.mock.calls,
      ...epMocks.ElMessage.error.mock.calls,
      ...epMocks.ElMessage.info.mock.calls,
      ...epMocks.ElMessage.warning.mock.calls,
    ]
    expect(allCalls.length, 'ElMessage 应被调').toBeGreaterThan(0)
    const lastMsg = allCalls.at(-1)?.[0] as string
    expect(lastMsg).toContain('89abcdef')
  })

  it('4a. A5001 → 404 空态 + 返回列表入口', async () => {
    apiMocks.getComplaint.mockRejectedValue({ message: '不存在', code: 'A5001', trace: 'tr_aaaa1111bbbb2222' })
    const wrapper = mountDetail(); await flushPromises()

    await findButton(wrapper, '返回列表').trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/complaints')
  })

  it('4b. A5002 → 提示 + 自动刷新详情', async () => {
    mockStatus('resolved') // 关单仅在 resolved 可达
    apiMocks.closeComplaint.mockRejectedValue({ message: '状态已变更', code: 'A5002', trace: 'tr_cccc3333dddd4444' })
    const wrapper = mountDetail(); await flushPromises()

    const beforeCalls = apiMocks.getComplaint.mock.calls.length
    await findButton(wrapper, '关单').trigger('click')
    await flushPromises(); await flushPromises() // 嵌套 async

    expect(epMocks.ElMessage.info).toHaveBeenCalled()
    expect(apiMocks.getComplaint.mock.calls.length).toBeGreaterThan(beforeCalls)
  })

  it('4c. B0010 → ElMessageBox 强提示', async () => {
    mockStatus('resolved') // 关单仅在 resolved 可达
    apiMocks.closeComplaint.mockRejectedValue({ message: '合规拦截', code: 'B0010', trace: 'tr_eeee5555ffff6666' })
    const wrapper = mountDetail(); await flushPromises()

    await findButton(wrapper, '关单').trigger('click')
    await flushPromises()

    expect(epMocks.ElMessageBox.alert).toHaveBeenCalled()
  })

  it('4d. A5015 → 正向提示 + 跳复核队列', async () => {
    mockStatus('pending_callback') // 回电登记仅在 pending_callback 可达
    apiMocks.submitDispositionCallback.mockRejectedValue({ message: '已转人工审核', code: 'A5015', trace: 'tr_0000aaaa1111bbbb' })
    const wrapper = mountDetail(); await flushPromises()

    await findButton(wrapper, '回电登记').trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.success).toHaveBeenCalled()
    expect(routerPush).toHaveBeenCalledWith('/complaints/review')
  })

  it('5. judge 失败但 task_status 仍 judging → 轮询继续（stopPolling 未被调用）', async () => {
    // 当前状态是 judging，轮询已启动（pollingMocks.isPolling = true）
    apiMocks.judgeComplaint.mockRejectedValue({ message: '网关错误', code: 'B0009', trace: 'tr_gggghhhh' })
    const wrapper = mountDetail(); await flushPromises()

    // 触发 judge reject
    await findButton(wrapper, '触发研判').trigger('click')
    await flushPromises()

    // D2 解耦：judge 失败只提示，不控制轮询
    expect(pollingMocks.stop).not.toHaveBeenCalled()
    expect(pollingMocks.isPolling.value).toBe(true)
    expect(pollingMocks.isStopped.value).toBe(false)
  })

  it('7. 操作区按 task_status 收敛：各状态只出现可达操作', async () => {
    // pending：只研判，无定责/复核/回电/关单
    mockStatus('pending')
    let wrapper = mountDetail(); await flushPromises()
    expect(wrapper.text()).toContain('触发研判')
    expect(wrapper.text()).not.toContain('提交定责')
    expect(wrapper.text()).not.toContain('提交复核')
    expect(wrapper.text()).not.toContain('回电登记')
    expect(wrapper.text()).not.toContain('关单')
    wrapper.unmount()

    // pending_review：研判+定责+复核，无回电/关单
    mockStatus('pending_review')
    wrapper = mountDetail(); await flushPromises()
    expect(wrapper.text()).toContain('提交定责')
    expect(wrapper.text()).toContain('提交复核')
    expect(wrapper.text()).not.toContain('回电登记')
    wrapper.unmount()

    // pending_callback：仅回电登记
    mockStatus('pending_callback')
    wrapper = mountDetail(); await flushPromises()
    expect(wrapper.text()).toContain('回电登记')
    expect(wrapper.text()).not.toContain('提交定责')
    expect(wrapper.text()).not.toContain('关单')
    wrapper.unmount()

    // resolved：仅关单
    mockStatus('resolved')
    wrapper = mountDetail(); await flushPromises()
    expect(wrapper.text()).toContain('关单')
    expect(wrapper.text()).not.toContain('回电登记')
    expect(wrapper.text()).not.toContain('提交定责')
    wrapper.unmount()

    // closed：无任何操作（空态提示）
    mockStatus('closed')
    wrapper = mountDetail(); await flushPromises()
    expect(wrapper.text()).toContain('当前状态无可用操作')
  })

  it('8. 入口锚点：?focus=judge 定位到研判块并高亮', async () => {
    mockStatus('pending')
    routeMocks.query = { focus: 'judge' }
    // jsdom 无 scrollIntoView，需打桩
    const scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy as unknown as () => void

    const wrapper = mount(ComplaintDetail, { global: { stubs: stubEP() }, attachTo: document.body })
    await flushPromises()
    await flushPromises()

    const judgeBlock = wrapper.find('#op-judge')
    expect(judgeBlock.exists()).toBe(true)
    // 应滚动到研判块
    expect(scrollSpy).toHaveBeenCalled()
    wrapper.unmount()
    routeMocks.query = {}
  })

  it('6. 详情页 mount→两次操作同 trace→unmount 清空（SSOT §9.4）', async () => {
    // 初始：request.ts 的 trace context 为空
    setTraceContext(null) // 确保干净起点
    expect(getTraceContext()).toBeNull()

    // mount ComplaintDetail → useTraceContext setup 自动写入
    apiMocks.judgeComplaint.mockResolvedValue({ complaint_id: 'c-001', task_status: 'judging', verdict: null, thread_id: null, message: 'ok' })
    const wrapper = mountDetail()
    await flushPromises()

    // mount 后 trace context 非空
    const traceAfterMount = getTraceContext()
    expect(traceAfterMount).not.toBeNull()

    // 触发 judge → getTraceContext() 值不变（拦截器每次读的都是同一值 → 两次请求带同一 X-Trace-Id）
    await findButton(wrapper, '触发研判').trigger('click')
    await flushPromises()
    expect(getTraceContext()).toBe(traceAfterMount)

    // unmount → useTraceContext onScopeDispose 自动清空
    wrapper.unmount()
    expect(getTraceContext()).toBeNull()
  })
})
