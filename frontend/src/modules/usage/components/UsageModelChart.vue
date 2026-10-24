<script setup lang="ts">
/**
 * 模型分布图（f3-usage-console 批次⑤ / tasks 5.1）。
 * `by_model` 为空时不渲染空图，改显示占位文案。
 */
import { computed, ref } from 'vue'
import type { EChartsCoreOption } from 'echarts/core'
import { useUsageChart } from '../composables/useUsageChart'
import type { UsageModelStat } from '../types'

const props = defineProps<{
  data: UsageModelStat[]
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
        data: props.data.map((row) => ({
          name: row.model,
          value: row.calls,
          tokens: row.tokens,
        })),
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
      <span>模型分布</span>
    </template>
    <el-empty
      v-if="!hasData"
      description="暂无模型分布数据"
      :image-size="60"
    />
    <div
      v-show="hasData"
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
