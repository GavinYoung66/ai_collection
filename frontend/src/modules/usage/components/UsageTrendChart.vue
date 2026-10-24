<script setup lang="ts">
/**
 * 调用量 × 失败率双轴趋势图（f3-usage-console 批次⑤ / tasks 5.2）。
 * 左轴调用量、右轴失败率（0~1 转百分比展示）；series 与 axis 配置可在单测断言。
 */
import { computed, ref } from 'vue'
import type { EChartsCoreOption } from 'echarts/core'
import { useUsageChart } from '../composables/useUsageChart'
import type { UsageTrendPoint } from '../types'

const props = defineProps<{
  data: UsageTrendPoint[]
}>()

const chartRef = ref<HTMLElement | null>(null)
const hasData = computed(() => props.data.length > 0)

/** ISO 时间 → 简写轴标签（不引入日期库） */
function formatBucket(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const option = computed<EChartsCoreOption | null>(() => {
  if (!hasData.value) return null
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    grid: { left: 48, right: 56, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: props.data.map((row) => formatBucket(row.bucket_start)),
    },
    yAxis: [
      { type: 'value', name: '调用量', minInterval: 1 },
      {
        type: 'value',
        name: '失败率',
        max: 1,
        axisLabel: { formatter: '{value}' },
      },
    ],
    series: [
      {
        name: '调用量',
        type: 'line',
        smooth: true,
        yAxisIndex: 0,
        data: props.data.map((row) => row.calls),
      },
      {
        name: '失败率',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        data: props.data.map((row) => row.failure_rate),
      },
    ],
  }
})

useUsageChart(chartRef, option)
</script>

<template>
  <el-card
    class="usage-chart-card"
    shadow="never"
  >
    <template #header>
      <span>调用量 × 失败率趋势</span>
    </template>
    <el-empty
      v-if="!hasData"
      description="暂无趋势数据"
      :image-size="60"
    />
    <div
      v-show="hasData"
      ref="chartRef"
      class="usage-chart usage-chart--trend"
    />
  </el-card>
</template>

<style scoped>
.usage-chart-card :deep(.el-card__body) {
  padding: 8px 12px 12px;
}

.usage-chart {
  width: 100%;
  height: 240px;
}

.usage-chart--trend {
  height: 280px;
}
</style>
