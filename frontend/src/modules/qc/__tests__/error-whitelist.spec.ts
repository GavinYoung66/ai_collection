/**
 * §15.1 错误码白名单模块侧动作集成测试（f2-qc-console 批次5 / task 7.1，design D6）。
 *
 * mock shared/request 经 api 层抛出的 RequestError 形态（构造方式照 shared/request.spec.ts
 * 与 task-board.spec.ts：message 已由拦截器附白名单文案 + trace 后 8 位，code/trace 挂字段）。
 *
 * 逐码覆盖：
 * - B0004 / A2005（详情 404 实际码，同动作）→ 详情空态 + 返回列表入口
 * - B0006 → 按 Retry-After 退避的可解释提示（响应头由拦截器消费，shared 层已测；
 *   模块侧断言提示文案含「系统繁忙」+ trace 后 8 位，且提供重试入口）
 * - A2002 → 人审表单行内标红（ReviewActions：输入框标红 + 行内文案，不弹全局错误）
 * - A2003 → 质检按钮置灰 + title 悬浮原因，不重复发起请求（断言方式复用任务台）
 * - 未映射 A20xx（A2009 等）→ 默认分支 message 直出 + trace 后 8 位，不新增映射
 * - A2011（LLM 降级，HTTP 200 信封 code 非 0）→ 走默认分支提示不置灰；
 *   降级标识经 trace 事件 status 逐字透传展示（TraceTimeline 不改词）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { RequestError } from '@/shared/request'

// ── Mocks（vi.mock 工厂提升，mock 函数必须经 vi.hoisted 定义）──
const apiMocks = vi.hoisted(() => ({
  getQcRecord: vi.fn(),
  getQcDemoTrace: vi.fn(),
  submitQcReview: vi.fn(),
  listQcRecords: vi.fn(),
  getQcRecordStats: vi.fn(),
  listQcDemoPending: vi.fn(),
  replayQcSession: vi.fn(),
  checkQcBatch: vi.fn(),
  seedQcDemo: vi.fn(),
  replayQcRaw: vi.fn(),
  listQcReviewsPending: vi.fn(),
  getQcRulesActive: vi.fn(),
  applyQcRules: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn(), alert: vi.fn() },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

const routerPush = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/qc/records/s-1', params: { sessionId: 's-1' }, query: {} }),
}))

import QcRecordDetail from '../views/QcRecordDetail.vue'
import QcRecordList from '../views/QcRecordList.vue'
import QcTaskBoard from '../views/QcTaskBoard.vue'
import QcRules from '../views/QcRules.vue'
import ReviewActions from '../components/ReviewActions.vue'
import TraceTimeline from '../components/TraceTimeline.vue'

/**
 * 构造拦截器形态的业务错误：message 已附白名单/后端文案 + trace 后 8 位
 * （shared/request.ts 响应拦截器输出口径：`${displayMessage}（trace: ${formatTrace(trace)}）`）
 */
function apiError(code: string, displayMessage: string, trace = 'tr_0123456789abcdef'): RequestError {
  const err = new Error(`${displayMessage}（trace: ${trace.slice(-8)}）`) as RequestError
  err.code = code
  err.trace = trace
  return err
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

function pendingItem(id: string) {
  return { session_id: id, scenario: 'A', label: `场景-${id}`, case_id: `case-${id}`, call_id: `call-${id}` }
}

beforeEach(() => {
  vi.clearAllMocks()
  epMocks.ElMessage.success = vi.fn()
  epMocks.ElMessage.error = vi.fn()
  epMocks.ElMessage.warning = vi.fn()
  epMocks.ElMessage.info = vi.fn()
  epMocks.ElMessageBox.confirm = vi.fn()
})

describe('§15.1 白名单：B0004/A2005 详情空态 + 返回列表', () => {
  function mountDetail(role = 'qc_reviewer') {
    return mount(QcRecordDetail, {
      global: { provide: { 'app:currentRole': role } },
    })
  }

  it('B0004 → 空态 + 返回列表入口', async () => {
    apiMocks.getQcRecord.mockRejectedValue(apiError('B0004', '请求的资源不存在'))
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('.qrd-empty').exists()).toBe(true)
    const backBtn = findButtonByText(wrapper, '返回列表')
    expect(backBtn).toBeDefined()
    await backBtn!.trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/qc/records')
  })

  it('A2005（qc 详情 404 实际码，同动作）→ 空态 + 返回列表入口', async () => {
    apiMocks.getQcRecord.mockRejectedValue(apiError('A2005', '请求的资源不存在'))
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('.qrd-empty').exists()).toBe(true)
    await findButtonByText(wrapper, '返回列表')!.trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/qc/records')
  })
})

describe('§15.1 白名单：B0006 按 Retry-After 退避提示', () => {
  it('列表加载收到 B0006 → 提示「系统繁忙」+ trace 后 8 位，提供重试入口', async () => {
    // 响应头 Retry-After 由拦截器消费并触发限流回调（shared/request.spec.ts 已测）；
    // 模块侧收到的是拦截器整形后的错误：白名单文案 + trace 后 8 位
    apiMocks.listQcRecords.mockRejectedValueOnce(apiError('B0006', '系统繁忙，稍后重试', 'tr_ccccdddd'))
    apiMocks.getQcRecordStats.mockResolvedValue({ from: null, to: null, group_by: 'day', items: [] })
    const wrapper = mount(QcRecordList)
    await flushPromises()

    const errorBox = wrapper.find('.pt-error')
    expect(errorBox.exists()).toBe(true)
    expect(errorBox.text()).toContain('系统繁忙，稍后重试')
    expect(errorBox.text()).toContain('ccccdddd') // trace 后 8 位
    // 幂等 GET 允许由按钮重试（§15.1：仅幂等 GET 允许调用方重试）
    apiMocks.listQcRecords.mockResolvedValueOnce({ items: [], total: 0, page: 1, page_size: 20 })
    await errorBox.find('button.pt-error-retry').trigger('click')
    await flushPromises()
    expect(apiMocks.listQcRecords).toHaveBeenCalledTimes(2)
  })
})

describe('§15.1 白名单：A2002 人审表单行内标红', () => {
  function mountActions() {
    return mount(ReviewActions, {
      props: { qcRecordId: 'rec-1', deductionTotal: 8, qcStatus: 'PENDING_REVIEW' },
      global: { provide: { 'app:currentRole': 'qc_reviewer' } },
    })
  }

  it('CORRECT 收到 A2002 → 输入框行内标红 + 行内文案，不弹全局错误（qc 的 422 不叠 B0001）', async () => {
    apiMocks.submitQcReview.mockRejectedValue(apiError('A2002', '质检入参不合法', 'tr_abcdef02'))
    const wrapper = mountActions()
    await findButtonByText(wrapper, '误报/改扣分')!.trigger('click')
    await wrapper.find('.qc-review-input').setValue('3')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    await flushPromises()

    // 行内标红：错误文案渲染 + 输入框标红 class；不走 ElMessage 全局错误
    const fieldError = wrapper.find('.qc-review-field-error')
    expect(fieldError.exists()).toBe(true)
    expect(fieldError.text()).toContain('质检入参不合法')
    expect(wrapper.find('.qc-review-input').classes()).toContain('qc-review-input--error')
    expect(epMocks.ElMessage.error).not.toHaveBeenCalled()
  })
})

describe('§15.1 白名单：A2003 质检按钮置灰 + 原因悬浮', () => {
  it('开检收到 A2003 → 该行按钮置灰 + title 悬浮后端原因，不重复发起请求', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.listQcRecords.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    apiMocks.replayQcSession.mockRejectedValue(
      apiError('A2003', '对话不可质检（通话未结束、全文未齐或通话主数据不存在）'),
    )
    const wrapper = mount(QcTaskBoard)
    await flushPromises()

    const btn = wrapper.find('tr[data-session-id="s-1"] button')
    await btn.trigger('click')
    await flushPromises()

    expect(apiMocks.replayQcSession).toHaveBeenCalledTimes(1)
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('title')).toContain('对话不可质检')
    await btn.trigger('click')
    await flushPromises()
    expect(apiMocks.replayQcSession).toHaveBeenCalledTimes(1) // 置灰后不重复请求
  })
})

describe('未映射码默认分支：message 直出 + trace 后 8 位，不新增映射', () => {
  it('A2009（无生效规则包，未列入白名单）→ 错误态 message 直出 + trace 后 8 位 + 重试', async () => {
    apiMocks.getQcRulesActive.mockRejectedValue(apiError('A2009', '当前无生效规则包', 'tr_9999eeee'))
    const wrapper = mount(QcRules)
    await flushPromises()

    const errorBox = wrapper.find('.qru-error')
    expect(errorBox.exists()).toBe(true)
    expect(errorBox.text()).toContain('当前无生效规则包') // message 直出
    expect(errorBox.text()).toContain('9999eeee') // trace 后 8 位
    // 提供重试入口
    apiMocks.getQcRulesActive.mockResolvedValueOnce({
      version: 'v1.0',
      status: 'active',
      penalty: { high: 20, medium: 8, low: 2 },
      same_word_window_ms: 30000,
      grade_thresholds: {},
      deduction_policy: {},
      effective_at: null,
    })
    await errorBox.find('button').trigger('click')
    await flushPromises()
    expect(apiMocks.getQcRulesActive).toHaveBeenCalledTimes(2)
  })

  it('A2011（LLM 降级，HTTP 200 信封 code 非 0）→ 默认分支提示，按钮不置灰可重试', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.listQcRecords.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    // 后端 A2011 信封 message 即码值本身（offline_qc_service.py:398），默认分支直出
    apiMocks.replayQcSession.mockRejectedValue(apiError('A2011', 'A2011', 'tr_degraded1'))
    const wrapper = mount(QcTaskBoard)
    await flushPromises()

    await wrapper.find('tr[data-session-id="s-1"] button').trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('A2011'))
    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('egraded1'.slice(-8)))
    // A2011 非 A2003：按钮不置灰，可重试（降级不阻断后续操作，design 风险条）
    const btn = wrapper.find('tr[data-session-id="s-1"] button')
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.attributes('title')).toBeUndefined()
  })

  it('降级标识展示：trace 事件 status 逐字透传（degraded 不改词）', () => {
    const wrapper = mount(TraceTimeline, {
      props: {
        events: [
          {
            category: 'qc',
            name: 'llm_review',
            started_at: '2026-10-23T00:00:01Z',
            duration_ms: 5000,
            status: 'degraded',
            summary: 'LLM 复核超时降级，本通仅 AC+规则结果',
          },
        ],
        langsmithUrl: null,
      },
    })
    expect(wrapper.text()).toContain('degraded')
    expect(wrapper.text()).toContain('LLM 复核超时降级')
  })
})
