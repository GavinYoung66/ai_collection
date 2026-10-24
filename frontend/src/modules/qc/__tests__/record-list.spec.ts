/**
 * QcRecordList 组件单测（f2-qc-console 批次4 / tasks 5.1）。
 *
 * 覆盖点：
 * - 默认分页参数：page=1、page_size=20（records.py:116-117 后端签名）
 * - 台账「扣分」列渲染 deduction_total；表头无合规分列与已退役评级列
 * - 统计卡：records/stats 返回字段（key/count/high_count、窗口）逐字透传渲染，前端不聚合
 * - 行点击跳详情 /qc/records/:sessionId；操作列按钮同样跳转
 * - keepAlive：onMounted 拉一次 + onActivated 刷新（首个激活去重）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// ── Mocks ──
const apiMocks = vi.hoisted(() => ({
  listQcRecords: vi.fn(),
  getQcRecordStats: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const routerPush = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/qc/records', params: {}, query: {} }),
}))

import QcRecordList from '../views/QcRecordList.vue'

function recordItem(id: string) {
  return {
    qc_record_id: `rec-${id}`,
    session_id: id,
    case_id: `case-${id}`,
    agent_id: 'agent-1',
    deduction_total: 8,
    compliance_score: 92,
    risk: 'medium' as const,
    grade: null,
    qc_result: 'violation' as const,
    qc_status: 'PENDING_REVIEW' as const,
    create_time: '2026-10-23T01:00:00Z',
  }
}

function statsResp() {
  return {
    from: '2026-10-16T00:00:00Z',
    to: '2026-10-23T00:00:00Z',
    group_by: 'day' as const,
    items: [
      { key: '2026-10-22', count: 3 },
      { key: '2026-10-23', count: 5 },
    ],
  }
}

function mountList() {
  return mount(QcRecordList)
}

describe('modules/qc/QcRecordList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.listQcRecords.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    apiMocks.getQcRecordStats.mockResolvedValue(statsResp())
  })

  it('默认分页参数 page=1、page_size=20；统计默认按日维度', async () => {
    mountList()
    await flushPromises()

    expect(apiMocks.listQcRecords).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
    )
    expect(apiMocks.getQcRecordStats).toHaveBeenCalledWith({ group_by: 'day' })
  })

  it('台账渲染扣分列（-8），表头无合规分列与已退役评级列', async () => {
    apiMocks.listQcRecords.mockResolvedValue({
      items: [recordItem('s-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountList()
    await flushPromises()

    const headText = wrapper.find('thead').text()
    expect(headText).toContain('扣分')
    expect(headText).not.toContain('合规分')

    const rowText = wrapper.find('tbody tr').text()
    expect(rowText).toContain('-8')
    expect(rowText).toContain('s-1')
    expect(rowText).toContain('待复核')
    expect(rowText).toContain('2026-10-23T01:00:00Z')
  })

  it('统计卡透传后端字段（key/count 逐项渲染 + 窗口回显），前端不聚合', async () => {
    const wrapper = mountList()
    await flushPromises()

    const cards = wrapper.findAll('.qrl-stat-card')
    expect(cards.length).toBe(2)
    expect(cards[0].attributes('data-stat-key')).toBe('2026-10-22')
    expect(cards[0].text()).toContain('3')
    expect(cards[1].attributes('data-stat-key')).toBe('2026-10-23')
    expect(cards[1].text()).toContain('5')
    // from/to 窗口与 group_by 透传回显
    expect(wrapper.find('.qrl-stats-window').text()).toContain('2026-10-16T00:00:00Z')
    expect(wrapper.find('.qrl-stats-window').text()).toContain('2026-10-23T00:00:00Z')
  })

  it('统计维度切换：按规则维度带 high_count 渲染', async () => {
    apiMocks.getQcRecordStats.mockResolvedValue({
      from: null,
      to: null,
      group_by: 'rule_id',
      items: [{ key: 'R05', count: 7, high_count: 4 }],
    })
    const wrapper = mountList()
    await flushPromises()
    apiMocks.getQcRecordStats.mockClear()

    await wrapper.find('select[aria-label="统计维度"]').setValue('rule_id')
    await flushPromises()

    expect(apiMocks.getQcRecordStats).toHaveBeenCalledWith({ group_by: 'rule_id' })
    const card = wrapper.find('.qrl-stat-card')
    expect(card.attributes('data-stat-key')).toBe('R05')
    expect(card.text()).toContain('7')
    expect(card.text()).toContain('高风险 4') // high_count 仅 rule_id 维度返回
  })

  it('行点击跳详情 /qc/records/:sessionId；操作列按钮同样跳转', async () => {
    apiMocks.listQcRecords.mockResolvedValue({
      items: [recordItem('s-9')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountList()
    await flushPromises()

    // 行点击（事件委托）
    await wrapper.find('tbody tr.pt-tr').trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/qc/records/s-9')

    // 操作列按钮（stop 修饰，不重复触发）
    routerPush.mockClear()
    const btn = wrapper.findAll('button').find((b) => b.text().includes('查看详情'))
    await btn!.trigger('click')
    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledWith('/qc/records/s-9')
  })

  it('onMounted 拉一次 + onActivated 再拉一次（首个激活去重）', async () => {
    const wrapper = mountList()
    await vi.waitFor(() => expect(apiMocks.listQcRecords).toHaveBeenCalledTimes(1))

    // 模拟 keepAlive 缓存激活：直接调用 setup 暴露的 fetchList
    const vm = wrapper.vm as unknown as Record<string, unknown>
    const setupState = (vm as { $?: { setupState?: Record<string, unknown> } }).$?.setupState
    const fetchList = setupState?.fetchList as (() => Promise<void>) | undefined
    expect(fetchList).toBeDefined()
    await fetchList!()
    await vi.waitFor(() => expect(apiMocks.listQcRecords).toHaveBeenCalledTimes(2))
  })
})
