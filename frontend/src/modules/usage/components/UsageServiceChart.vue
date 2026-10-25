<script setup lang="ts">
/**
 * 服务分布图（f3-usage-console 批次⑤ / tasks 5.3，design D4）。
 * `by_service` 是结构性缺失项（llm_call_logs 无 svc 列）：
 * 缺失或为空时由父级整块不渲染（UsageDashboard v-if），本组件只在有数据时挂载。
 */
import { computed, ref } from 'vue'
import type { EChartsCoreOption } from 'echarts/core'
import { useUsageChart } from '../composables/useUsageChart'
import type { UsageServiceStat } from '../types'

const props = defineProps<{
  data: UsageServiceStat[]
}>()

const chartRef = ref<HTMLElement | null>(null)
const hasData = computed(() => props.data.length > 0)

const option = computed<EChartsCoreOption | null>(() => {
  if (!hasData.value) return null
  return {
    tooltip: { trigger: 'item' },
    legend: { type: 'scroll', bottom: 0 },
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '45%'],
        data: props.data.map((row) => ({ name: row.service, value: row.calls })),
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
      <span>服务分布</span>
    </template>
    <div
      ref="chartRef"
      class="usage-chart"
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
</style>
