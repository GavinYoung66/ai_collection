/**
 * QcReview + ReviewActions + QcRules + QcAlerts 组件单测（f2-qc-console 批次5 / tasks 6.1-6.4）。
 *
 * 覆盖点：
 * - QcReview：进入页面调 reviews/pending（page=1、page_size=20）；空态；行内嵌 ReviewActions；
 *   submitted 后刷新队列
 * - ReviewActions 三态（独立挂载，角色经 inject 桥 provide 裸角色字符串）：
 *   零扣分（deduction_total=0 或缺省）不渲染提交按钮；CONFIRM 直提不带 revised_deduction；
 *   CORRECT 必填 revised_deduction（>=0 整数，空/非数字/负数均拦截不发请求）；
 *   REVIEWED 禁用；角色 ∉ {qc_reviewer, supervisor} 置灰+说明
 * - ReviewActions 模板不出现后端三值枚举中的驳回动作名（?raw 源码佐证）
 * - QcRules：rules/active 只读渲染（无任何编辑控件）；热应用需 ElMessageBox 二次确认，
 *   取消不发请求，确认后才调 rules/apply；失败可解释提示
 * - QcAlerts：alerts 列表渲染；ack 防连点（在途仅一次请求）；成功后就地更新该条状态；
 *   已确认行禁用确认按钮
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// ── Mocks（vi.mock 工厂提升，mock 函数必须经 vi.hoisted 定义）──
const apiMocks = vi.hoisted(() => ({
  listQcReviewsPending: vi.fn(),
  submitQcReview: vi.fn(),
  getQcRulesActive: vi.fn(),
  applyQcRules: vi.fn(),
  listQcAlerts: vi.fn(),
  ackQcAlert: vi.fn(),
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
  useRoute: () => ({ path: '/qc/reviews', params: {}, query: {} }),
}))

import QcReview from '../views/QcReview.vue'
import QcRules from '../views/QcRules.vue'
import QcAlerts from '../views/QcAlerts.vue'
import ReviewActions from '../components/ReviewActions.vue'
// ?raw 导入组件源码，佐证模板不出现后端三值枚举中的驳回动作名
import REVIEW_ACTIONS_SOURCE from '../components/ReviewActions.vue?raw'

// ── 固定数据 ──
function pendingItem(id: string, over: Record<string, unknown> = {}) {
  return {
    qc_record_id: `rec-${id}`,
    session_id: id,
    case_id: `case-${id}`,
    compliance_score: 80,
    deduction_total: 8,
    risk: 'medium' as const,
    grade: null,
    qc_result: 'violation' as const,
    qc_status: 'PENDING_REVIEW' as const,
    ...over,
  }
}

function rulesActiveResp() {
  return {
    version: 'v1.0',
    status: 'active',
    penalty: { high: 20, medium: 8, low: 2 },
    same_word_window_ms: 30000,
    grade_thresholds: {},
    deduction_policy: {
      R01: { clause_no: '红线手册-6.1', clause_text: '禁止向无关第三方泄露债务人隐私信息。' },
      R13: { clause_no: '红线手册-2.1', clause_text: '客户要求停止通话须立即停止。' },
      R14: { clause_no: '红线手册-3.5', clause_text: '禁止夸大法律后果。' },
    },
    effective_at: '2026-10-01T00:00:00Z',
  }
}

function alertItem(id: string, over: Record<string, unknown> = {}) {
  return {
    alert_id: id,
    qc_record_id: `rec-${id}`,
    session_id: `s-${id}`,
    level: 'high' as const,
    status: 'NEW' as const,
    evidence: '你再不还钱我们就上门找你',
    create_time: '2026-10-23T01:00:00Z',
    ...over,
  }
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

/** 挂载人审操作组；role 经 inject 桥注入（生产由 AdminLayout provide ComputedRef） */
function mountActions(
  props: { qcRecordId?: string; deductionTotal?: number; qcStatus?: string } = {},
  role = 'qc_reviewer',
) {
  return mount(ReviewActions, {
    props: {
      qcRecordId: 'rec-1',
      deductionTotal: 8,
      qcStatus: 'PENDING_REVIEW',
      ...props,
    } as never,
    global: { provide: { 'app:currentRole': role } },
  })
}

const reviewedResp = {
  qc_record_id: 'rec-1',
  action: 'CONFIRM',
  qc_status: 'REVIEWED',
  deduction_total: 8,
  compliance_score: 80,
  qc_result: 'violation',
  risk: 'high',
  grade: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  epMocks.ElMessage.success = vi.fn()
  epMocks.ElMessage.error = vi.fn()
  epMocks.ElMessage.warning = vi.fn()
  epMocks.ElMessage.info = vi.fn()
  epMocks.ElMessageBox.confirm = vi.fn()
  epMocks.ElMessageBox.alert = vi.fn()
})

describe('modules/qc/QcReview', () => {
  beforeEach(() => {
    apiMocks.listQcReviewsPending.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  })

  function mountReview(role = 'qc_reviewer') {
    return mount(QcReview, {
      global: { provide: { 'app:currentRole': role } },
    })
  }

  it('进入页面调 reviews/pending（page=1、page_size=20）并渲染关键字段', async () => {
    apiMocks.listQcReviewsPending.mockResolvedValue({
      items: [pendingItem('s-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountReview()
    await flushPromises()

    expect(apiMocks.listQcReviewsPending).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
    )
    const text = wrapper.text()
    expect(text).toContain('s-1')
    expect(text).toContain('case-s-1')
    expect(text).toContain('-8') // 扣分列
    expect(text).toContain('待复核') // qc_status
    // 行内嵌人审操作组（复用 ReviewActions）
    expect(wrapper.find('.qc-review-actions').exists()).toBe(true)
  })

  it('空队列渲染空态', async () => {
    const wrapper = mountReview()
    await flushPromises()
    expect(wrapper.text()).toContain('暂无待复核记录')
  })

  it('行内 ReviewActions 提交成功（submitted）后刷新队列', async () => {
    apiMocks.listQcReviewsPending.mockResolvedValue({
      items: [pendingItem('s-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    apiMocks.submitQcReview.mockResolvedValue(reviewedResp)
    const wrapper = mountReview('supervisor')
    await flushPromises()
    apiMocks.listQcReviewsPending.mockClear()

    await findButtonByText(wrapper, '扣分正确')!.trigger('click')
    await flushPromises()

    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-s-1', { action: 'CONFIRM' })
    expect(apiMocks.listQcReviewsPending).toHaveBeenCalledTimes(1) // submitted 后刷新队列
  })
})

describe('modules/qc/ReviewActions 三态', () => {
  it('零扣分（deduction_total=0）：不渲染提交按钮', () => {
    const wrapper = mountActions({ deductionTotal: 0, qcStatus: 'DONE' })
    expect(findButtonByText(wrapper, '扣分正确')).toBeUndefined()
    expect(findButtonByText(wrapper, '误报/改扣分')).toBeUndefined()
    expect(wrapper.text()).toContain('零扣分已完成，无需人审')
  })

  it('零扣分缺省（deduction_total 缺省，fail-closed）：不渲染提交按钮', () => {
    const wrapper = mountActions({ deductionTotal: undefined, qcStatus: 'DONE' })
    expect(findButtonByText(wrapper, '扣分正确')).toBeUndefined()
    expect(findButtonByText(wrapper, '误报/改扣分')).toBeUndefined()
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('CONFIRM 直提：不校验/不携带 revised_deduction', async () => {
    apiMocks.submitQcReview.mockResolvedValue(reviewedResp)
    const wrapper = mountActions()
    await findButtonByText(wrapper, '扣分正确')!.trigger('click')
    await flushPromises()

    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-1', { action: 'CONFIRM' })
    const body = apiMocks.submitQcReview.mock.calls[0][1] as Record<string, unknown>
    expect('revised_deduction' in body).toBe(false)
  })

  it('CORRECT：revised_deduction 必填且 >=0 整数，非法输入拦截不发请求', async () => {
    apiMocks.submitQcReview.mockResolvedValue({ ...reviewedResp, action: 'CORRECT' })
    const wrapper = mountActions()
    await findButtonByText(wrapper, '误报/改扣分')!.trigger('click')

    // 空值拦截
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('>=0 整数'))
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()

    // 非数字拦截
    await wrapper.find('.qc-review-input').setValue('abc')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()

    // 负数拦截（正则仅允许数字串）
    await wrapper.find('.qc-review-input').setValue('-1')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()

    // 合法整数提交
    await wrapper.find('.qc-review-input').setValue('5')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    await flushPromises()
    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-1', {
      action: 'CORRECT',
      revised_deduction: 5,
    })
  })

  it('CORRECT：改后扣分等于机检扣分时拦截不发请求（旧页 app.js:717-721 行为平移）', async () => {
    const wrapper = mountActions({ deductionTotal: 8 })
    await findButtonByText(wrapper, '误报/改扣分')!.trigger('click')
    await wrapper.find('.qc-review-input').setValue('8')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining('仍等于机检扣分'),
    )
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('REVIEWED：全部人审按钮禁用，点击不发请求', async () => {
    const wrapper = mountActions({ qcStatus: 'REVIEWED' })
    const confirmBtn = findButtonByText(wrapper, '扣分正确')
    const correctBtn = findButtonByText(wrapper, '误报/改扣分')
    expect(confirmBtn?.attributes('disabled')).toBeDefined()
    expect(correctBtn?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('本通已人审')
    await confirmBtn?.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it.each(['admin', 'agent', 'ai_trainer'])('角色 %s 不在复核词表：置灰 + 说明，不发起请求', async (role) => {
    const wrapper = mountActions({}, role)
    const confirmBtn = findButtonByText(wrapper, '扣分正确')
    expect(confirmBtn?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('仅 qc_reviewer/supervisor 可复核')
    await confirmBtn?.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('组件模板不出现后端三值枚举中的驳回动作名（?raw 源码佐证）', () => {
    // 拼接构造检索词，避免本测试自身被 grep 误命中
    const forbidden = ['DIS', 'MISS'].join('')
    expect(REVIEW_ACTIONS_SOURCE).not.toContain(forbidden)
  })
})

describe('modules/qc/QcRules', () => {
  beforeEach(() => {
    apiMocks.getQcRulesActive.mockResolvedValue(rulesActiveResp())
  })

  function mountRules() {
    return mount(QcRules)
  }

  it('进入页面调 rules/active 并只读渲染版本/档位/规则行（R01~R14 由 deduction_policy 键透传）', async () => {
    const wrapper = mountRules()
    await flushPromises()

    expect(apiMocks.getQcRulesActive).toHaveBeenCalledTimes(1)
    const text = wrapper.text()
    expect(text).toContain('v1.0')
    expect(text).toContain('active')
    // 扣分档位透传
    expect(text).toContain('-20')
    expect(text).toContain('-8')
    expect(text).toContain('-2')
    // 规则行透传
    expect(wrapper.find('tr[data-rule-id="R01"]').exists()).toBe(true)
    expect(wrapper.find('tr[data-rule-id="R13"]').text()).toContain('客户要求停止通话须立即停止')
    expect(wrapper.find('tr[data-rule-id="R14"]').exists()).toBe(true)
  })

  it('只读列表：页面无任何编辑/新增/删除控件', async () => {
    const wrapper = mountRules()
    await flushPromises()

    expect(findButtonByText(wrapper, '编辑')).toBeUndefined()
    expect(findButtonByText(wrapper, '新增')).toBeUndefined()
    expect(findButtonByText(wrapper, '删除')).toBeUndefined()
    // 规则表格行内无输入控件（唯一输入框为热应用目标版本号）
    const rulesTable = wrapper.find('table.qru-rules')
    expect(rulesTable.findAll('input').length).toBe(0)
  })

  it('热应用必须二次确认：取消不发请求，确认后调 rules/apply 并刷新', async () => {
    const wrapper = mountRules()
    await flushPromises()

    await wrapper.find('.qru-input').setValue('v1.1')

    // 用户取消：不发起请求
    epMocks.ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await findButtonByText(wrapper, '热应用')!.trigger('click')
    await flushPromises()
    expect(epMocks.ElMessageBox.confirm).toHaveBeenCalled()
    expect(apiMocks.applyQcRules).not.toHaveBeenCalled()

    // 用户确认：发起 rules/apply 并刷新生效版本
    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.applyQcRules.mockResolvedValueOnce({
      version: 'v1.1',
      status: 'active',
      previous_version: 'v1.0',
    })
    apiMocks.getQcRulesActive.mockClear()
    await findButtonByText(wrapper, '热应用')!.trigger('click')
    await flushPromises()

    expect(apiMocks.applyQcRules).toHaveBeenCalledWith({ version: 'v1.1' })
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('v1.0'))
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('v1.1'))
    expect(apiMocks.getQcRulesActive).toHaveBeenCalledTimes(1)
  })

  it('热应用失败：可解释提示（message 直出）', async () => {
    const wrapper = mountRules()
    await flushPromises()
    await wrapper.find('.qru-input').setValue('v9.9')

    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.applyQcRules.mockRejectedValueOnce(new Error('规则版本不存在（trace: abc12345）'))
    await findButtonByText(wrapper, '热应用')!.trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('规则版本不存在'))
  })
})

describe('modules/qc/QcAlerts', () => {
  beforeEach(() => {
    apiMocks.listQcAlerts.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  })

  function mountAlerts() {
    return mount(QcAlerts)
  }

  it('进入页面调 alerts（page=1、page_size=20）并渲染告警字段', async () => {
    apiMocks.listQcAlerts.mockResolvedValue({
      items: [alertItem('a-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountAlerts()
    await flushPromises()

    expect(apiMocks.listQcAlerts).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
    )
    const text = wrapper.text()
    expect(text).toContain('a-1')
    expect(text).toContain('high')
    expect(text).toContain('未确认')
    expect(text).toContain('你再不还钱我们就上门找你')
  })

  it('ack 确认：提交中防连点（在途仅一次请求），成功后就地更新该条状态', async () => {
    apiMocks.listQcAlerts.mockResolvedValue({
      items: [alertItem('a-1'), alertItem('a-2')],
      total: 2,
      page: 1,
      page_size: 20,
    })
    // 挂起模拟在途
    apiMocks.ackQcAlert.mockReturnValue(new Promise(() => {}))
    const wrapper = mountAlerts()
    await flushPromises()

    const ackBtns = wrapper.findAll('button.qal-action')
    expect(ackBtns.length).toBe(2)
    await ackBtns[0].trigger('click')
    await ackBtns[0].trigger('click')
    await ackBtns[0].trigger('click')

    expect(apiMocks.ackQcAlert).toHaveBeenCalledTimes(1)
    expect(apiMocks.ackQcAlert).toHaveBeenCalledWith('a-1')
    expect(ackBtns[0].attributes('disabled')).toBeDefined()
  })

  it('ack 成功：该条状态更新为已确认，按钮禁用', async () => {
    apiMocks.listQcAlerts.mockResolvedValue({
      items: [alertItem('a-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    apiMocks.ackQcAlert.mockResolvedValue({
      alert_id: 'a-1',
      status: 'ACKED',
      acked_by: 'admin',
      acked_at: '2026-10-23T02:00:00Z',
    })
    const wrapper = mountAlerts()
    await flushPromises()

    await wrapper.find('button.qal-action').trigger('click')
    await flushPromises()

    expect(apiMocks.ackQcAlert).toHaveBeenCalledWith('a-1')
    expect(wrapper.text()).toContain('已确认')
    expect(wrapper.find('button.qal-action').attributes('disabled')).toBeDefined()
  })

  it('已确认行（status=ACKED，以后端字段为准）禁用确认按钮，点击不发请求', async () => {
    apiMocks.listQcAlerts.mockResolvedValue({
      items: [alertItem('a-9', { status: 'ACKED' })],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountAlerts()
    await flushPromises()

    const btn = wrapper.find('button.qal-action')
    expect(btn.attributes('disabled')).toBeDefined()
    await btn.trigger('click')
    expect(apiMocks.ackQcAlert).not.toHaveBeenCalled()
  })
})
