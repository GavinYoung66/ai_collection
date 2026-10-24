/**
 * 图表配置与生命周期单测（f3-usage-console 批次⑧ / tasks 8.4）。
 *
 * mock ../echarts（jsdom 无 canvas）：断言 setOption 的 series/xAxis/yAxis 入参、
 * 数据变化重绘、卸载 dispose + resize 监听移除、KeepAlive 激活后重绘并 resize。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

const chartMock = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))
vi.mock('../echarts', () => ({ default: { init: () => chartMock } }))

import UsageModelChart from '../components/UsageModelChart.vue'
import UsageTrendChart from '../components/UsageTrendChart.vue'
import UsageServiceChart from '../components/UsageServiceChart.vue'

interface ChartOptionLike {
  xAxis?: { data?: string[] }
  yAxis?: Array<{ name?: string; max?: number }>
  series?: Array<{
    name?: string
    yAxisIndex?: number
    data?: Array<number | { name: string; value: number }>
  }>
}

function lastOption(): ChartOptionLike {
  const calls = chartMock.setOption.mock.calls
  expect(calls.length).toBeGreaterThan(0)
  return calls[calls.length - 1]![0] as ChartOptionLike
}

/** EP 组件在图表单测中未安装插件，统一置空 */
const STUBS = { 'el-card': { template: '<div><slot name="header" /><slot /></div>' }, 'el-empty': true }

const TREND = [
  { bucket_start: '2026-10-21T00:00:00Z', calls: 10, failure_rate: 0.1 },
  { bucket_start: '2026-10-22T00:00:00Z', calls: 20, failure_rate: 0.2 },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('modules/usage/charts', () => {
  it('趋势图：双轴配置（左调用量 / 右失败率 max=1）与 series 数据', async () => {
    mount(UsageTrendChart, { props: { data: TREND }, global: { stubs: STUBS } })
    await flushPromises()

    const opt = lastOption()
    expect(opt.xAxis?.data).toHaveLength(2)
    expect(opt.yAxis?.[0]?.name).toBe('调用量')
    expect(opt.yAxis?.[1]?.name).toBe('失败率')
    expect(opt.yAxis?.[1]?.max).toBe(1)
    expect(opt.series?.[0]?.data).toEqual([10, 20])
    expect(opt.series?.[1]?.data).toEqual([0.1, 0.2])
    expect(opt.series?.[1]?.yAxisIndex).toBe(1)
  })

  it('趋势图：数据变化 → 重新 setOption', async () => {
    const wrapper = mount(UsageTrendChart, { props: { data: TREND }, global: { stubs: STUBS } })
    await flushPromises()
    const before = chartMock.setOption.mock.calls.length

    await wrapper.setProps({
      data: [...TREND, { bucket_start: '2026-10-23T00:00:00Z', calls: 30, failure_rate: 0.05 }],
    })
    await flushPromises()
    expect(chartMock.setOption.mock.calls.length).toBeGreaterThan(before)
    expect(lastOption().series?.[0]?.data).toEqual([10, 20, 30])
  })

  it('模型分布：饼图 data 取 calls，name 取 model', async () => {
    mount(UsageModelChart, {
      props: { data: [{ model: 'qwen-plus', calls: 96, tokens: 72000 }] },
      global: { stubs: STUBS },
    })
    await flushPromises()

    expect(lastOption().series?.[0]?.data).toEqual([
      { name: 'qwen-plus', value: 96, tokens: 72000 },
    ])
  })

  it('服务分布：饼图 data 取 service/calls', async () => {
    mount(UsageServiceChart, {
      props: { data: [{ service: 'qc', calls: 128 }] },
      global: { stubs: STUBS },
    })
    await flushPromises()

    expect(lastOption().series?.[0]?.data).toEqual([{ name: 'qc', value: 128 }])
  })

  it('卸载：dispose 实例并移除 window resize 监听', async () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const wrapper = mount(UsageModelChart, {
      props: { data: [{ model: 'qwen-plus', calls: 1, tokens: 1 }] },
      global: { stubs: STUBS },
    })
    await flushPromises()

    wrapper.unmount()
    expect(chartMock.dispose).toHaveBeenCalledTimes(1)
    expect(removeSpy).toHaveBeenCalledWith('resize', expect.any(Function))
    removeSpy.mockRestore()
  })

  it('KeepAlive 激活：重绘并 resize（容器尺寸恢复）', async () => {
    const Host = defineComponent({
      components: { UsageModelChart },
      props: { show: Boolean },
      template:
        '<KeepAlive><UsageModelChart v-if="show" :data="[{ model: \'m\', calls: 1, tokens: 1 }]" /></KeepAlive>',
    })
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const wrapper = mount(Host, { props: { show: true }, global: { stubs: STUBS } })
    await flushPromises()
    await wrapper.setProps({ show: false })
    // 失活即摘除 resize 监听（spec R4「卸载或失活时移除尺寸监听」）
    expect(removeSpy).toHaveBeenCalledWith('resize', expect.any(Function))
    const before = chartMock.setOption.mock.calls.length

    await wrapper.setProps({ show: true })
    await flushPromises()
    expect(chartMock.setOption.mock.calls.length).toBeGreaterThan(before)
    expect(chartMock.resize).toHaveBeenCalled()
    removeSpy.mockRestore()
  })
})
