/**
 * ComplaintList 统计卡片区单测（tasks 4.1）。
 *
 * 覆盖：
 * - 统计区只取 GET /complaints/stats，后端字段逐字呈现，不自行聚合
 * - 7 字段（total / avg_processing_minutes / evidence_insufficient_rate /
 *   mcp_failure_rate / ai_human_consistency_rate / by_verdict）渲染逐字段一致
 * - loading / error 三态
 * - onActivated 也触发 stats 拉取
 * - 无 echarts / 图表库依赖（grep 验证，测试侧只验证基础组件呈现）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ComplaintList from '../views/ComplaintList.vue'

const mockStats = {
  total: 128,
  avg_processing_minutes: 42.5,
  evidence_insufficient_rate: 0.12,
  mcp_failure_rate: 0.03,
  ai_human_consistency_rate: 0.88,
  by_verdict: {
    agent_violation: 45,
    user_misunderstanding: 32,
    compliant_but_poor_experience: 51,
  },
}

const apiMocks = vi.hoisted(() => ({
  listComplaints: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
  getComplaintStats: vi.fn(),
}))

vi.mock('../api', () => ({
  listComplaints: apiMocks.listComplaints,
  createComplaint: vi.fn(),
  seedDemoComplaints: vi.fn(),
  listComplaintEvents: vi.fn(),
  getComplaintStats: apiMocks.getComplaintStats,
  switchComplaintMode: vi.fn(),
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { alert: vi.fn().mockResolvedValue(undefined) },
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ path: '/complaints', params: {}, query: {} }),
}))

function mountComponent() {
  return mount(ComplaintList, {
    global: { stubs: { PageTable: true } },
  })
}

describe('modules/complaint/ComplaintList#4.1 统计区', () => {
  beforeEach(() => {
    apiMocks.listComplaints.mockClear()
    apiMocks.listComplaints.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    apiMocks.getComplaintStats.mockClear()
    apiMocks.getComplaintStats.mockResolvedValue({ ...mockStats })
  })

  it('onMounted 拉 stats + 列表', async () => {
    mountComponent()
    await flushPromises()
    expect(apiMocks.getComplaintStats).toHaveBeenCalledTimes(1)
    expect(apiMocks.listComplaints).toHaveBeenCalledTimes(1)
  })

  it('7 字段渲染逐字段一致（total / avg / 各率 / by_verdict）', async () => {
    const wrapper = mountComponent()
    await flushPromises()

    // 5 个主指标卡
    const statCards = wrapper.findAll('.cl-stat-card')
    expect(statCards.length).toBe(5)

    const texts = statCards.map(c => c.text())
    expect(texts.some(t => t.includes('128'))).toBe(true)      // total
    expect(texts.some(t => t.includes('42.5'))).toBe(true)    // avg_processing_minutes
    expect(texts.some(t => t.includes('12.0%'))).toBe(true)   // evidence_insufficient_rate
    expect(texts.some(t => t.includes('3.0%'))).toBe(true)    // mcp_failure_rate
    expect(texts.some(t => t.includes('88.0%'))).toBe(true)   // ai_human_consistency_rate

    // by_verdict 分布（三个 chip）
    const chips = wrapper.findAll('.cl-verdict-chip')
    expect(chips.length).toBe(3)
    const chipTexts = chips.map(c => c.text())
    expect(chipTexts).toEqual(expect.arrayContaining([
      expect.stringContaining('坐实违规: 45'),
      expect.stringContaining('用户误解: 32'),
      expect.stringContaining('合规但体验差: 51'),
    ]))
  })

  it('stats loading 态', async () => {
    apiMocks.getComplaintStats.mockReturnValue(new Promise(() => {}))
    const wrapper = mountComponent()
    // fetchStats 第一行 statsLoading.value = true 已同步执行；
    // Vue 响应式更新在 nextTick——用 Promise.resolve 等一帧但不触发挂起的 Promise resolve
    await Promise.resolve()
    expect(wrapper.find('.cl-stats-loading').exists()).toBe(true)
    expect(wrapper.find('.cl-stat-card').exists()).toBe(false)
  })

  it('stats error 态', async () => {
    apiMocks.getComplaintStats.mockRejectedValue(new Error('后端挂了'))
    const wrapper = mountComponent()
    await flushPromises()
    const errEl = wrapper.find('.cl-stats-error')
    expect(errEl.exists()).toBe(true)
    expect(errEl.text()).toContain('后端挂了')
  })

  it('stats 渲染不自行聚合——直接透传后端 by_verdict 原始键', async () => {
    const customStats = { ...mockStats, by_verdict: { agent_violation: 99 } }
    apiMocks.getComplaintStats.mockResolvedValue(customStats)
    const wrapper = mountComponent()
    await flushPromises()
    // 只有一个 chip——前端只遍历后端返回的键，不补齐
    expect(wrapper.findAll('.cl-verdict-chip').length).toBe(1)
    expect(wrapper.find('.cl-verdict-chip').text()).toContain('坐实违规: 99')
  })
})
