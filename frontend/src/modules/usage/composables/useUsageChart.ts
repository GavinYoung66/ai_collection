/**
 * 图表生命周期基座（f3-usage-console 批次⑤ / tasks 5.4，design D6）。
 *
 * 三个图表组件共用：onMounted 初始化 + 首次 setOption；数据变化重绘；
 * onActivated（keepAlive 返回）重绘并按容器尺寸 resize；window resize 跟随；
 * onDeactivated 摘除监听、onBeforeUnmount 摘除监听并 dispose，避免残留。
 * 空数据时不 init（组件根节点用 v-if/v-show 控制，见各图表）。
 */
import {
  nextTick,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  watch,
  type Ref,
} from 'vue'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import echarts from '../echarts'

export function useUsageChart(
  container: Ref<HTMLElement | null>,
  option: Ref<EChartsCoreOption | null>,
): { render: () => Promise<void>; resize: () => void } {
  let chart: ECharts | null = null
  let listening = false

  /** 容器可能延迟出现（by_service 缺失整块隐藏），故 await nextTick 后再 init */
  async function render(): Promise<void> {
    await nextTick()
    if (!container.value) return
    if (!chart) chart = echarts.init(container.value)
    if (option.value) chart.setOption(option.value, true)
  }

  function resize(): void {
    chart?.resize()
  }

  function onWindowResize(): void {
    resize()
  }

  /** 监听幂等挂载/摘除：mounted→activated 会连续触发，避免重复 add */
  function attach(): void {
    if (listening) return
    window.addEventListener('resize', onWindowResize)
    listening = true
  }

  function detach(): void {
    if (!listening) return
    window.removeEventListener('resize', onWindowResize)
    listening = false
  }

  onMounted(() => {
    attach()
    void render()
  })

  /** keepAlive 返回：容器尺寸在隐藏期间失真，重绘后必须 resize */
  onActivated(async () => {
    attach()
    await render()
    resize()
  })

  /** keepAlive 失活：移除尺寸监听，避免对隐藏画布做无意义 resize（spec R4） */
  onDeactivated(() => {
    detach()
  })

  watch(option, () => void render(), { deep: true })

  onBeforeUnmount(() => {
    detach()
    chart?.dispose()
    chart = null
  })

  return { render, resize }
}
