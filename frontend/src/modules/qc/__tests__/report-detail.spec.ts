/**
 * QcRecordDetail + ReviewActions + TraceTimeline 组件单测（f2-qc-console 批次4 / tasks 5.2-5.4）。
 *
 * 覆盖点：
 * - 头部字段渲染：扣分/合规分/风险/质检状态/质检结论；模板零「评级字段」残留（?raw 源码佐证）
 * - 命中表：来源/规则/证据/本条扣分渲染；客户侧 R13（score_eligible=false）标注「不计入」
 * - kb_snippet 与 policy_text 分区渲染不混用（.qc-hit-policy vs .qc-kb-section）
 * - skipped_rules 文案含「本通未审」且区块内不出现「已合规」表述
 * - 会话证据对照：命中词 <mark> 高亮
 * - trace 时间线：无 langsmith_url 不渲染外链，有才渲染
 * - 人审按钮组（ReviewActions 完整逻辑）：零扣分组不渲染按钮、REVIEWED 禁用、
 *   agent 置灰+说明、admin 可用、CONFIRM 直提、CORRECT 缺 revised_deduction 前端拦截不发请求
 * - B0004/A2005 → 空态 + 返回列表
 *
 * 注：角色经 provide/inject 桥注入（key 见 shared/permissions.ts，生产由 AdminLayout provide），
 * 测试直接 provide 裸角色字符串。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// ── Mocks（vi.mock 工厂提升，mock 函数必须经 vi.hoisted 定义）──
const apiMocks = vi.hoisted(() => ({
  getQcRecord: vi.fn(),
  getQcDemoTrace: vi.fn(),
  submitQcReview: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

const routerPush = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/qc/records/s-1', params: { sessionId: 's-1' }, query: {} }),
}))

import QcRecordDetail from '../views/QcRecordDetail.vue'
import { CURRENT_ROLE_INJECT_KEY } from '@/shared'
// ?raw 导入视图源码，佐证模板无已退役评级字段残留
import DETAIL_SOURCE from '../views/QcRecordDetail.vue?raw'

// ── 固定数据 ──
function agentHit() {
  return {
    turn_id: 't-1',
    rule_id: 'R05',
    word_id: 'w-1',
    severity: 'high' as const,
    redline_tag: 'threat' as const,
    speaker: 'agent' as const,
    score_eligible: true,
    evidence_text: '你再不还钱我们就上门找你',
    source: 'rule' as const,
    word: '上门',
    points: 20,
    policy_clause: '催收守则第 3.2 条',
    policy_text: '禁止以威胁方式催收',
    kb_snippet: '知识库原文：上门催收须持双证并提前预约',
  }
}

function r13Hit() {
  return {
    turn_id: 't-2',
    rule_id: 'R13',
    word_id: null,
    severity: 'low' as const,
    redline_tag: null,
    speaker: 'user' as const,
    score_eligible: false,
    evidence_text: '不要再打了',
    source: 'ac' as const,
    word: '不要再打了',
    points: 0,
    policy_clause: null,
    policy_text: null,
    kb_snippet: null,
  }
}

function detailResp(over: Record<string, unknown> = {}) {
  return {
    qc_record_id: 'rec-1',
    session_id: 's-1',
    case_id: 'case-1',
    agent_id: 'agent-1',
    deduction_total: 20,
    compliance_score: 80,
    risk: 'high' as const,
    grade: null,
    qc_result: 'violation' as const,
    qc_status: 'PENDING_REVIEW' as const,
    high_count: 1,
    med_count: 0,
    low_count: 1,
    hits: [agentHit(), r13Hit()],
    skipped_rules: ['R07', 'R09'],
    ...over,
  }
}

function traceResp(over: Record<string, unknown> = {}) {
  return {
    session_id: 's-1',
    call_id: 'call-1',
    events: [
      {
        category: 'qc',
        name: 'ac_scan',
        started_at: '2026-10-23T00:00:00Z',
        duration_ms: 12,
        status: 'ok',
        summary: 'AC 扫描完成',
      },
    ],
    langsmith_url: null,
    mcp_call_qc: 'not_called',
    mcp_call_qc_note: '本通未调用 MCP call_qc',
    ...over,
  }
}

/** 挂载详情页；role 经 inject 桥注入（生产由 AdminLayout provide ComputedRef） */
function mountDetail(role = 'qc_reviewer') {
  return mount(QcRecordDetail, {
    global: { provide: { [CURRENT_ROLE_INJECT_KEY]: role } },
  })
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

describe('modules/qc/QcRecordDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    epMocks.ElMessage.success = vi.fn()
    epMocks.ElMessage.error = vi.fn()
    epMocks.ElMessage.warning = vi.fn()
    epMocks.ElMessage.info = vi.fn()
    apiMocks.getQcRecord.mockResolvedValue(detailResp())
    apiMocks.getQcDemoTrace.mockResolvedValue(traceResp())
  })

  it('头部字段渲染：扣分/合规分/风险/质检状态/质检结论；按 sessionId 拉详情与 trace', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    expect(apiMocks.getQcRecord).toHaveBeenCalledWith('s-1')
    expect(apiMocks.getQcDemoTrace).toHaveBeenCalledWith('s-1')

    const text = wrapper.text()
    expect(text).toContain('-20') // deduction_total
    expect(text).toContain('80') // compliance_score
    expect(text).toContain('高') // risk
    expect(text).toContain('待复核') // qc_status
    expect(text).toContain('违规') // qc_result
    expect(text).toContain('高 1 / 中 0 / 低 1') // 命中统计
  })

  it('模板零评级字段残留（?raw 源码佐证，验证口径：全文件无该词）', () => {
    // 拼接构造检索词，避免本测试自身被 grep 误命中
    const forbidden = ['gr', 'ade'].join('')
    expect(DETAIL_SOURCE).not.toContain(forbidden)
  })

  it('命中表：来源/规则/证据/扣分渲染；客户侧 R13 标注「不计入」', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    const hitsTable = wrapper.find('table.qc-hits')
    expect(hitsTable.exists()).toBe(true)
    const rows = hitsTable.findAll('tbody tr')
    expect(rows.length).toBe(2)

    // 坐席侧命中：来源文案 + 规则码 + 证据 + 本条扣分
    expect(rows[0].text()).toContain('规则引擎')
    expect(rows[0].text()).toContain('R05')
    expect(rows[0].text()).toContain('你再不还钱我们就上门找你')
    expect(rows[0].text()).toContain('-20')
    // 制度依据单元格：policy_clause + policy_text，且不得混入 kb_snippet
    const policyCell = rows[0].find('.qc-hit-policy')
    expect(policyCell.text()).toContain('催收守则第 3.2 条')
    expect(policyCell.text()).toContain('禁止以威胁方式催收')
    expect(policyCell.text()).not.toContain('知识库原文')

    // 客户侧 R13：标注「客户侧 R13 不计入」，扣分列显示不计入
    expect(rows[1].text()).toContain('R13')
    expect(rows[1].text()).toContain('客户侧 R13 不计入')
    expect(rows[1].find('.qc-hit-points').text()).toContain('不计入')
  })

  it('kb_snippet 独立区块展示，与 policy_text 分区不混用', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    const kbSection = wrapper.find('.qc-kb-section')
    expect(kbSection.exists()).toBe(true)
    expect(kbSection.text()).toContain('知识库原文：上门催收须持双证并提前预约')
    // kb 区块不得混入静态制度条款
    expect(kbSection.text()).not.toContain('禁止以威胁方式催收')
  })

  it('skipped_rules 文案含「本通未审」，区块内无「已合规」误导表述', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    const skipped = wrapper.find('.qc-skipped')
    expect(skipped.exists()).toBe(true)
    expect(skipped.text()).toContain('R07')
    expect(skipped.text()).toContain('本通未审')
    expect(skipped.text()).not.toContain('已合规')
  })

  it('会话证据对照：说话方/轮次呈现，命中词 <mark> 高亮', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    const items = wrapper.findAll('.qc-transcript-item')
    expect(items.length).toBe(2)
    expect(items[0].text()).toContain('坐席')
    expect(items[0].text()).toContain('t-1')
    const mark = items[0].find('mark.qc-evidence-mark')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe('上门')
    // 客户侧行
    expect(items[1].text()).toContain('客户')
  })

  it('trace 无 langsmith_url：时间线渲染但不出现外链', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('.qc-trace-timeline').exists()).toBe(true)
    expect(wrapper.text()).toContain('ac_scan')
    expect(wrapper.find('a.qc-langsmith-link').exists()).toBe(false)
  })

  it('trace 含 langsmith_url：渲染 LangSmith 外链', async () => {
    apiMocks.getQcDemoTrace.mockResolvedValue(
      traceResp({ langsmith_url: 'https://smith.langchain.com/run/abc' }),
    )
    const wrapper = mountDetail()
    await flushPromises()

    const link = wrapper.find('a.qc-langsmith-link')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('https://smith.langchain.com/run/abc')
  })

  it('零扣分（deduction_total=0）：不渲染人审提交按钮', async () => {
    apiMocks.getQcRecord.mockResolvedValue(
      detailResp({ deduction_total: 0, qc_status: 'DONE', qc_result: 'compliant', hits: [] }),
    )
    const wrapper = mountDetail()
    await flushPromises()

    expect(findButtonByText(wrapper, '扣分正确')).toBeUndefined()
    expect(findButtonByText(wrapper, '误报/改扣分')).toBeUndefined()
    expect(wrapper.text()).toContain('零扣分已完成，无需人审')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('qc_status=REVIEWED：人审按钮全部禁用', async () => {
    apiMocks.getQcRecord.mockResolvedValue(detailResp({ qc_status: 'REVIEWED' }))
    const wrapper = mountDetail()
    await flushPromises()

    const confirmBtn = findButtonByText(wrapper, '扣分正确')
    const correctBtn = findButtonByText(wrapper, '误报/改扣分')
    expect(confirmBtn?.attributes('disabled')).toBeDefined()
    expect(correctBtn?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('本通已人审')
    // 禁用态点击不发请求
    await confirmBtn?.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('agent 角色：人审按钮组置灰 + 说明，不发起请求', async () => {
    const wrapper = mountDetail('agent')
    await flushPromises()

    const confirmBtn = findButtonByText(wrapper, '扣分正确')
    expect(confirmBtn?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('仅 admin/qc_reviewer/supervisor 可复核')
    await confirmBtn?.trigger('click')
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()
  })

  it('admin 角色（最高权限）：人审按钮可用且可提交复核', async () => {
    apiMocks.submitQcReview.mockResolvedValue({
      qc_record_id: 'rec-1',
      action: 'CONFIRM',
      qc_status: 'REVIEWED',
      deduction_total: 20,
      compliance_score: 80,
      qc_result: 'violation',
      risk: 'high',
      grade: null,
    })
    const wrapper = mountDetail('admin')
    await flushPromises()

    const confirmBtn = findButtonByText(wrapper, '扣分正确')
    expect(confirmBtn?.attributes('disabled')).toBeUndefined()
    await confirmBtn!.trigger('click')
    await flushPromises()
    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-1', { action: 'CONFIRM' })
  })

  it('CONFIRM 直提：调 reviews/{qc_record_id} 并刷新详情', async () => {
    apiMocks.submitQcReview.mockResolvedValue({
      qc_record_id: 'rec-1',
      action: 'CONFIRM',
      qc_status: 'REVIEWED',
      deduction_total: 20,
      compliance_score: 80,
      qc_result: 'violation',
      risk: 'high',
      grade: null,
    })
    const wrapper = mountDetail('supervisor')
    await flushPromises()
    apiMocks.getQcRecord.mockClear()

    await findButtonByText(wrapper, '扣分正确')!.trigger('click')
    await flushPromises()

    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-1', { action: 'CONFIRM' })
    expect(apiMocks.getQcRecord).toHaveBeenCalledTimes(1) // 提交成功后刷新详情
  })

  it('CORRECT：未填 revised_deduction 前端拦截不发请求；填整数后提交', async () => {
    apiMocks.submitQcReview.mockResolvedValue({
      qc_record_id: 'rec-1',
      action: 'CORRECT',
      qc_status: 'REVIEWED',
      deduction_total: 10,
      compliance_score: 90,
      qc_result: 'violation',
      risk: 'high',
      grade: null,
    })
    const wrapper = mountDetail()
    await flushPromises()

    // 进入改扣分模式，直接提交 → 必填拦截
    await findButtonByText(wrapper, '误报/改扣分')!.trigger('click')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('>=0 整数'))
    expect(apiMocks.submitQcReview).not.toHaveBeenCalled()

    // 填合法整数后提交
    await wrapper.find('.qc-review-input').setValue('10')
    await findButtonByText(wrapper, '提交人审')!.trigger('click')
    await flushPromises()
    expect(apiMocks.submitQcReview).toHaveBeenCalledWith('rec-1', {
      action: 'CORRECT',
      revised_deduction: 10,
    })
  })

  it('A2005/B0004：空态 + 返回列表入口', async () => {
    apiMocks.getQcRecord.mockRejectedValue({
      message: '请求的资源不存在（trace: abc12345）',
      code: 'A2005',
      trace: 'tr_abc12345',
    })
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('.qrd-empty').exists()).toBe(true)
    const backBtn = findButtonByText(wrapper, '返回列表')
    expect(backBtn).toBeDefined()
    await backBtn!.trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/qc/records')
  })
})
