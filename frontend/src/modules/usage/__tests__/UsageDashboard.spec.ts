/**
 * UsageDashboard 批次④ 用例（tasks 4.2-4.3）：时间范围重取 + 看板空态。
 * 批次⑥/⑦ 再补明细、隐藏、横幅与错误分支。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  getUsageSummary: vi.fn(),
  listUsageLogs: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

/** jsdom 无 canvas：图表实例全部打桩，只关心组件是否挂载与配置入参 */
const chartMocks = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))
vi.mock('../echarts', () => ({
  default: { init: () => chartMocks },
}))

import UsageDashboard from '../views/UsageDashboard.vue'
import UsageServiceChart from '../components/UsageServiceChart.vue'
import type { UsageSummaryResponse } from '../types'

function summaryResp(over: Partial<UsageSummaryResponse> = {}): UsageSummaryResponse {
  return {
    totals: { calls: 120, tokens: 45000, avg_latency_ms: 830.4, failure_rate: 0.0333 },
    by_model: [{ model: 'qwen-plus', calls: 120, tokens: 45000 }],
    trend: [{ bucket_start: '2026-10-23T00:00:00Z', calls: 120, failure_rate: 0.0333 }],
    ...over,
  }
}

function dashboardStubs() {
  return {
    'el-radio-group': {
      name: 'ElRadioGroup',
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<div class="radio-group"><slot /></div>',
    },
    'el-radio-button': {
      props: ['value'],
      template: '<button class="radio"><slot /></button>',
    },
    'el-date-picker': {
      props: ['modelValue'],
      emits: ['update:model-value'],
      template: '<div class="date-picker" />',
    },
    'el-empty': {
      props: ['description'],
      template: '<div class="empty">{{ description }}</div>',
    },
    'el-alert': {
      props: ['title'],
      template: '<div class="alert">{{ title }}</div>',
    },
    'el-card': {
      template: '<div class="card"><slot name="header" /><slot /></div>',
    },
    'el-button': {
      name: 'ElButton',
      props: ['size', 'type'],
      emits: ['click'],
      template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
    },
    'el-tabs': {
      name: 'ElTabs',
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<div class="tabs"><slot /></div>',
    },
    'el-tab-pane': {
      props: ['label', 'name', 'lazy'],
      template: '<div class="tab-pane"><slot /></div>',
    },
    // 明细表单独在 UsageLogTable.spec.ts 覆盖，此处只关心看板
    UsageLogTable: true,
  }
}

function mountDashboard() {
  return mount(UsageDashboard, {
    global: {
      stubs: dashboardStubs(),
      // EP 指令在单测中不安装插件，空指令消除 v-loading 解析告警
      directives: { loading: {} },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getUsageSummary.mockResolvedValue(summaryResp())
})

describe('modules/usage/UsageDashboard 批次④', () => {
  it('进入页面拉一次汇总，start/end 均下发', async () => {
    mountDashboard()
    await flushPromises()
    expect(apiMocks.getUsageSummary).toHaveBeenCalledTimes(1)
    const query = apiMocks.getUsageSummary.mock.calls[0][0]
    expect(query.start).toBeTruthy()
    expect(query.end).toBeTruthy()
  })

  it('切换近 30 天 → 携带新窗口重新取数', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    const group = wrapper.findComponent({ name: 'ElRadioGroup' })
    group.vm.$emit('update:modelValue', '30d')
    await flushPromises()

    expect(apiMocks.getUsageSummary).toHaveBeenCalledTimes(2)
    const [first, second] = apiMocks.getUsageSummary.mock.calls.map((c) => c[0])
    const span = new Date(second.end).getTime() - new Date(second.start).getTime()
    expect(second.start).not.toBe(first.start)
    expect(span).toBeGreaterThanOrEqual(29 * 24 * 60 * 60 * 1000)
    expect(span).toBeLessThanOrEqual(31 * 24 * 60 * 60 * 1000)
  })

  it('totals.calls === 0 → 空态文案，不渲染指标卡', async () => {
    apiMocks.getUsageSummary.mockResolvedValue(
      summaryResp({
        totals: { calls: 0, tokens: 0, avg_latency_ms: null, failure_rate: 0 },
        by_model: [],
        trend: [],
      }),
    )
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('所选时间范围内暂无调用记录')
    expect(wrapper.find('.usage-stat-cards').exists()).toBe(false)
  })

  it('有数据 → 渲染四项指标值（时延取整、失败率百分比）', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('120')
    expect(wrapper.text()).toContain('45,000')
    expect(wrapper.text()).toContain('830 ms')
    expect(wrapper.text()).toContain('3.33%')
  })

  it('by_service 缺失 → 服务分布整块隐藏（不渲染空卡片）', async () => {
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.findComponent(UsageServiceChart).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('服务分布')
  })

  it('by_service 存在 → 服务分布渲染', async () => {
    apiMocks.getUsageSummary.mockResolvedValue(
      summaryResp({ by_service: [{ service: 'qc', calls: 120 }] }),
    )
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.findComponent(UsageServiceChart).exists()).toBe(true)
    expect(wrapper.text()).toContain('服务分布')
  })

  it('常驻横幅与数据状态无关：空态下依然展示', async () => {
    apiMocks.getUsageSummary.mockResolvedValue(
      summaryResp({
        totals: { calls: 0, tokens: 0, avg_latency_ms: null, failure_rate: 0 },
        by_model: [],
        trend: [],
      }),
    )
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('仅 qc-service 上报')
  })

  it('resilience 缺失 → 韧性块整块隐藏；存在 → 展示三项计数', async () => {
    const missing = mountDashboard()
    await flushPromises()
    expect(missing.text()).not.toContain('韧性降级')

    apiMocks.getUsageSummary.mockResolvedValue(
      summaryResp({ resilience: { rate_limited: 7, breaker_open: 8, fallback: 9 } }),
    )
    const present = mountDashboard()
    await flushPromises()
    expect(present.text()).toContain('韧性降级')
    expect(present.text()).toContain('限流')
    expect(present.text()).toContain('9')
  })

  it('B0006/B0009 命中白名单文案：页内展示 + 提供重试，不自动轮询', async () => {
    apiMocks.getUsageSummary.mockRejectedValueOnce(
      new Error('系统繁忙，稍后重试（trace: abc12345）'),
    )
    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('系统繁忙，稍后重试')
    expect(wrapper.text()).toContain('重试')
    // 失败不触发额外请求
    expect(apiMocks.getUsageSummary).toHaveBeenCalledTimes(1)
    // 指标卡与图表不渲染（不展示伪空数据）
    expect(wrapper.find('.usage-stat-cards').exists()).toBe(false)

    // 手动重试才再次取数
    wrapper.findComponent({ name: 'ElButton' }).trigger('click')
    await flushPromises()
    expect(apiMocks.getUsageSummary).toHaveBeenCalledTimes(2)
  })
})
