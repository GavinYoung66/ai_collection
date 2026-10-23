/**
 * ComplaintDetail 组件单测（tasks 3.1 骨架 + 后续操作组/Tab 组覆盖）。
 *
 * 批次 3.1 覆盖：
 * - judging 状态步骤条高亮
 * - AI vs 人审改判对照展示（不一致 + 改判原因）
 * - A5001 工单不存在 → 404 空态 + 返回列表入口
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  getComplaint: vi.fn(),
  getComplaintVerdict: vi.fn(),
  getComplaintTrace: vi.fn(),
  getComplaintAgentLogs: vi.fn(),
}))

const routerPushMock = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  getComplaint: apiMocks.getComplaint,
  getComplaintVerdict: apiMocks.getComplaintVerdict,
  getComplaintTrace: apiMocks.getComplaintTrace,
  getComplaintAgentLogs: apiMocks.getComplaintAgentLogs,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPushMock }),
  useRoute: () => ({
    path: '/complaints/c-001',
    params: { id: 'c-001' },
    query: {},
  }),
}))

import { mount } from '@vue/test-utils'
import ComplaintDetail from '../views/ComplaintDetail.vue'

/** Element Plus 组件 stub（带 class/template 以便 query） */
function stubAllElementPlus() {
  const tagStub = { template: '<span class="ep-tag"><slot /></span>' }
  const buttonStub = {
    template: '<button class="ep-button" :disabled="disabled || loading" type="button"><slot /></button>',
    props: ['disabled', 'loading', 'type'],
  }
  return {
    'el-skeleton': { template: '<div class="ep-skeleton" />' },
    'el-empty': { template: '<div class="ep-empty"><slot /></div>' },
    'el-alert': { template: '<div class="ep-alert" />' },
    'el-steps': { template: '<div class="ep-steps"><slot /></div>' },
    'el-step': { template: '<div class="ep-step" />' },
    'el-tag': tagStub,
    'el-descriptions': { template: '<div class="ep-descriptions"><slot /></div>' },
    'el-descriptions-item': { template: '<div class="ep-descriptions-item"><slot /></div>' },
    'el-button': buttonStub,
    // el-tabs：加 name 以便 findAllComponents 定位；emit change 事件来模拟 Tab 切换
    'el-tabs': {
      name: 'ElTabs',
      props: ['modelValue'],
      template: '<div class="ep-tabs"><slot /></div>',
      emits: ['update:modelValue', 'tab-change'],
    },
    'el-tab-pane': { props: ['name', 'label'], template: '<div class="ep-tab-pane"><slot /></div>' },
  }
}

function mountDetail() {
  return mount(ComplaintDetail, {
    global: { stubs: stubAllElementPlus() },
  })
}

const mockJudgingDetail = {
  complaint_id: 'c-001',
  complaint_text: '客户投诉态度不好',
  task_status: 'judging' as const,
  verdict: null,
  ai_verdict: null,
  disposition_verdict: null,
  disposition_reason: null,
  callback_scheduled: false,
  ordered_evidence: [],
  evidence_refs: [],
  confidence: null,
  response_draft: null,
  created_at: '2026-10-23T10:00:00Z',
  updated_at: '2026-10-23T10:00:00Z',
}

const mockVerdict = {
  complaint_id: 'c-001',
  verdict: null,
  confidence: null,
  response_draft: null,
  regulation_refs: [],
  rag_source_ids: [],
  evidence_refs: [],
}

describe('modules/complaint/ComplaintDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('1. judging 状态步骤条高亮「研判中」节点', async () => {
    apiMocks.getComplaint.mockResolvedValue(mockJudgingDetail)
    apiMocks.getComplaintVerdict.mockResolvedValue(mockVerdict)

    const wrapper = mountDetail()
    await flushPromises()

    expect(apiMocks.getComplaint).toHaveBeenCalledWith('c-001')

    const tags = wrapper.findAll('.ep-tag')
    const stateTag = tags.find(t => t.text().includes('研判中'))
    expect(stateTag, 'judging 应显示「研判中」标签').toBeTruthy()

    const steps = wrapper.findAll('.ep-step')
    expect(steps.length).toBe(11)
  })

  it('2. AI 初选与人审定责不一致时展示改判对照', async () => {
    const changedDetail = {
      ...mockJudgingDetail,
      task_status: 'pending_review' as const,
      ai_verdict: 'agent_violation' as const,
      disposition_verdict: 'compliant_but_poor_experience' as const,
      disposition_reason: 'AI 判定过重，实际问题主要在体验层面',
    }
    apiMocks.getComplaint.mockResolvedValue(changedDetail)
    apiMocks.getComplaintVerdict.mockResolvedValue({
      ...mockVerdict,
      verdict: changedDetail.ai_verdict,
      confidence: 0.82,
      rag_source_ids: ['rag_doc_001', 'rag_doc_002'],
      response_draft: '您好，非常抱歉给您带来不好的体验…',
    })

    const wrapper = mountDetail()
    await flushPromises()

    // 定责类型展示中文标签（不再直出枚举原值）
    expect(wrapper.text()).toContain('坐实违规')
    expect(wrapper.text()).toContain('合规但体验差')
    expect(wrapper.text()).not.toContain('agent_violation')
    expect(wrapper.find('.verdict-changed').exists(), '改判时人审终值应带 changed class').toBe(true)
    expect(wrapper.text()).toContain('AI 判定过重，实际问题主要在体验层面')
  })

  it('3. A5001 工单不存在 → 404 空态 + 返回列表入口', async () => {
    apiMocks.getComplaint.mockRejectedValue({
      message: '投诉工单不存在',
      code: 'A5001',
      trace: 'tr_0123456789abcdef',
    })

    const wrapper = mountDetail()
    await flushPromises()

    // A5001 → 渲染 ep-empty 空态
    expect(wrapper.find('.ep-empty').exists(), 'A5001 应渲染 el-empty 空态').toBe(true)

    // 返回列表按钮
    const backBtn = wrapper.findAll('.ep-button').find(b => b.text().includes('返回列表'))
    expect(backBtn, '应提供返回列表按钮').toBeTruthy()

    await backBtn!.trigger('click')
    expect(routerPushMock).toHaveBeenCalledWith('/complaints')
  })

  it('4. Tab 按需加载：trace 详情加载即拉取（供 AI 取证链），agent-logs 切换后才发请求', async () => {
    apiMocks.getComplaint.mockResolvedValue(mockJudgingDetail)
    apiMocks.getComplaintVerdict.mockResolvedValue(mockVerdict)
    apiMocks.getComplaintTrace.mockResolvedValue({ complaint_id: 'c-001', events: [], langsmith_url: null })
    apiMocks.getComplaintAgentLogs.mockResolvedValue({ complaint_id: 'c-001', logs: [] })

    const wrapper = mountDetail()
    await flushPromises()

    // trace：详情加载即拉取（AI 产出物区展示取证链所需）
    expect(apiMocks.getComplaintTrace).toHaveBeenCalledWith('c-001')
    // agent-logs：仍按需（未切换时零请求）
    expect(apiMocks.getComplaintAgentLogs).not.toHaveBeenCalled()

    // 从 wrapper 的子组件中找到 el-tabs stub，直接 emit tab-change（绕过 DOM trigger）
    const childTabs = wrapper.findAllComponents({ name: 'ElTabs' }).at(0)
    expect(childTabs, '应找到 el-tabs 组件实例').toBeTruthy()
    const tabsWrapper = childTabs! as unknown as { vm: { $emit: (e: string, ...args: unknown[]) => void } }

    // 切换到 agent-logs Tab → 发请求
    tabsWrapper.vm.$emit('tab-change', 'agent-logs')
    await flushPromises()
    expect(apiMocks.getComplaintAgentLogs).toHaveBeenCalledWith('c-001')
    // 再切回 trace → 不重复请求（loaded 标记）
    apiMocks.getComplaintTrace.mockClear()
    tabsWrapper.vm.$emit('tab-change', 'trace')
    await flushPromises()
    expect(apiMocks.getComplaintTrace).not.toHaveBeenCalled()
  })
})
