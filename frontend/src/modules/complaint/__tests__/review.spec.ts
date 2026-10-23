/**
 * ComplaintReview 复核工作台单测（左右分栏独立页）。
 *
 * 覆盖点：
 * - 左栏队列：首次请求固定携带 WAITING_HUMAN_TASK_STATUS 集合（不可改筛选）
 * - 不复用 ComplaintList，不提供触发研判/建单等接入侧操作
 * - 点选工单 → 右栏拉详情 + trace 取证链，原地展示草稿（不跳详情页）
 * - 操作按 task_status 收敛：pending_review 有定责/复核，pending_callback 仅回电
 * - 复核动作下拉为中文标签，提交时传后端口径原值
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  listComplaints: vi.fn(),
  getComplaint: vi.fn(),
  getComplaintTrace: vi.fn(),
  submitDispositionVerdict: vi.fn(),
  submitDispositionCallback: vi.fn(),
  reviewComplaint: vi.fn(),
}))

vi.mock('../api', () => apiMocks)
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
const routerPush = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/complaints/review', params: {}, query: {} }),
}))

import ComplaintReview from '../views/ComplaintReview.vue'
import ComplaintList from '../views/ComplaintList.vue'
import { WAITING_HUMAN_TASK_STATUS } from '../types'

const QUEUE_ITEM = {
  complaint_id: 'case-v2-review-0001',
  complaint_text: '坐席态度恶劣，要求处理',
  task_status: 'pending_review' as const,
  verdict: 'agent_violation' as const,
  created_at: '2026-10-23T10:00:00Z',
}

function detailOf(status: string) {
  return {
    complaint_id: QUEUE_ITEM.complaint_id,
    complaint_text: QUEUE_ITEM.complaint_text,
    task_status: status,
    verdict: 'agent_violation',
    ai_verdict: 'agent_violation',
    disposition_verdict: null,
    disposition_reason: null,
    callback_scheduled: false,
    ordered_evidence: [],
    evidence_refs: [],
    confidence: 0.92,
    response_draft: '经核查，坐席存在不当话术，建议致歉并按流程升级。',
    created_at: '2026-10-23T10:00:00Z',
    updated_at: '2026-10-23T10:05:00Z',
  }
}

const STUB = {
  'el-button': { template: '<button type="button" @click="$emit(\'click\')"><slot /></button>' },
  'el-tag': { template: '<span class="ep-tag"><slot /></span>' },
  'el-alert': { template: '<div class="ep-alert">{{ title }}</div>', props: ['title'] },
  'el-empty': { template: '<div class="ep-empty">{{ description }}</div>', props: ['description', 'imageSize'] },
  'el-skeleton': { template: '<div class="ep-skeleton" />' },
  'el-select': {
    template: '<select class="ep-select" :value="modelValue"><slot /></select>',
    props: ['modelValue'],
  },
  'el-option': { template: '<option :value="value" :label="label" />', props: ['value', 'label'] },
  'el-input': {
    template: '<input class="ep-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue', 'placeholder'],
  },
}

function mountPage() {
  return mount(ComplaintReview, { global: { stubs: STUB } })
}

describe('modules/complaint/ComplaintReview（左右分栏复核工作台）', () => {
  beforeEach(() => {
    apiMocks.listComplaints.mockReset()
    apiMocks.getComplaint.mockReset()
    apiMocks.getComplaintTrace.mockReset()
    apiMocks.submitDispositionVerdict.mockReset()
    apiMocks.submitDispositionCallback.mockReset()
    apiMocks.reviewComplaint.mockReset()
    routerPush.mockClear()

    apiMocks.listComplaints.mockResolvedValue({
      items: [QUEUE_ITEM], total: 1, page: 1, page_size: 100,
    })
    apiMocks.getComplaint.mockResolvedValue(detailOf('pending_review'))
    apiMocks.getComplaintTrace.mockResolvedValue({
      complaint_id: QUEUE_ITEM.complaint_id,
      events: [
        { category: 'MCP', name: 'biz.get_case', started_at: '', duration_ms: 1, status: 'ok', summary: '拉取案件摘要' },
      ],
      langsmith_url: null,
    })
  })

  it('左栏队列固定请求 WAITING_HUMAN_TASK_STATUS，且不复用 ComplaintList', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMocks.listComplaints).toHaveBeenCalledWith(
      expect.objectContaining({ task_status: [...WAITING_HUMAN_TASK_STATUS] }),
    )
    // 独立页面：内部不再出现通用列表组件
    expect(wrapper.findComponent(ComplaintList).exists()).toBe(false)
    // 队列渲染出一条
    expect(wrapper.findAll('.rw-item')).toHaveLength(1)
  })

  it('不提供触发研判 / 建单 / 演示灌数等接入侧操作', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.rw-item').trigger('click')
    await flushPromises()

    const texts = wrapper.findAll('button').map((b) => b.text())
    expect(texts.some((t) => t.includes('研判'))).toBe(false)
    expect(texts.some((t) => t.includes('建单'))).toBe(false)
    expect(texts.some((t) => t.includes('演示灌数'))).toBe(false)
    // 右栏只在原地处理，不自动跳详情
    expect(routerPush).not.toHaveBeenCalled()
  })

  it('点选队列 → 右栏拉详情+trace，原地展示取证链与草稿', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.rw-item').trigger('click')
    await flushPromises()

    expect(apiMocks.getComplaint).toHaveBeenCalledWith(QUEUE_ITEM.complaint_id)
    expect(apiMocks.getComplaintTrace).toHaveBeenCalledWith(QUEUE_ITEM.complaint_id)

    const text = wrapper.text()
    expect(text).toContain('AI 取证链（1 条）')
    expect(text).toContain('biz.get_case')
    expect(text).toContain('经核查，坐席存在不当话术')
    // 定责/复核表单可见（pending_review）
    expect(text).toContain('定责确认')
    expect(text).toContain('人工复核')
    expect(text).not.toContain('回电登记')
  })

  it('复核动作下拉为中文标签', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.rw-item').trigger('click')
    await flushPromises()

    const labels = wrapper.findAll('option').map((o) => o.attributes('label'))
    expect(labels).toEqual(expect.arrayContaining(['通过', '改判', '改回应', '驳回重研判']))
    // 不应出现英文枚举
    expect(labels.join(',')).not.toContain('approve')
  })

  it('pending_callback 状态：只有回电登记，无定责/复核', async () => {
    apiMocks.getComplaint.mockResolvedValue(detailOf('pending_callback'))
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.rw-item').trigger('click')
    await flushPromises()

    // 按区块标题判断（队列状态标签里也含「待人工复核」字样，不能用全文 includes）
    const blockTitles = wrapper.findAll('.rw-block h4').map((h) => h.text())
    expect(blockTitles.some((t) => t.includes('回电登记'))).toBe(true)
    expect(blockTitles.some((t) => t.includes('定责确认'))).toBe(false)
    expect(blockTitles.some((t) => t.includes('人工复核'))).toBe(false)
  })

  it('定责提交传后端枚举原值，成功后重拉详情并刷新队列', async () => {
    apiMocks.submitDispositionVerdict.mockResolvedValue({
      complaint_id: QUEUE_ITEM.complaint_id, task_status: 'pending_callback',
    })
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.rw-item').trigger('click')
    await flushPromises()

    // 直接给表单赋值（el-select stub 无法模拟选择）
    const vm = wrapper.vm as unknown as {
      verdictForm: { verdict: string; reason: string }
      submitVerdict: () => Promise<void>
    }
    vm.verdictForm.verdict = 'agent_violation'
    vm.verdictForm.reason = '证据充分'
    apiMocks.listComplaints.mockClear()
    apiMocks.getComplaint.mockClear()

    await vm.submitVerdict()
    await flushPromises()

    expect(apiMocks.submitDispositionVerdict).toHaveBeenCalledWith(
      QUEUE_ITEM.complaint_id,
      { establishment: 'agent_violation', reason: '证据充分' },
    )
    // 成功后：重拉详情 + 刷新队列
    expect(apiMocks.getComplaint).toHaveBeenCalled()
    expect(apiMocks.listComplaints).toHaveBeenCalled()
  })
})
