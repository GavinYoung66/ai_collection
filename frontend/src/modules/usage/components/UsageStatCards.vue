<script setup lang="ts">
/**
 * 四项核心指标卡（f3-usage-console 批次④ / tasks 4.1）。
 *
 * 纯展示组件：props 进、无 api 调用（design D5）；`totals=null`（首屏加载前）显示占位。
 * `failure_rate` 为 0~1 小数，此处仅做百分比格式化，不改后端聚合口径。
 */
import { computed } from 'vue'
import type { UsageTotals } from '../types'

const props = defineProps<{
  totals: UsageTotals | null
}>()

const cards = computed(() => {
  const t = props.totals
  return [
    { key: 'calls', label: '调用量', value: t ? t.calls.toLocaleString() : '—' },
    { key: 'tokens', label: 'token 总量', value: t ? t.tokens.toLocaleString() : '—' },
    {
      key: 'latency',
      label: '平均时延',
      value: t && t.avg_latency_ms !== null ? `${Math.round(t.avg_latency_ms)} ms` : '—',
    },
    {
      key: 'failure',
      label: '失败率',
      value: t ? `${(t.failure_rate * 100).toFixed(2)}%` : '—',
    },
  ]
})
</script>

<template>
  <div class="usage-stat-cards">
    <el-card
      v-for="card in cards"
      :key="card.key"
      class="usage-stat-card"
      shadow="never"
    >
      <div class="usage-stat-card__label">
        {{ card.label }}
      </div>
      <div
        class="usage-stat-card__value"
        :class="{ 'is-placeholder': card.value === '—' }"
      >
        {{ card.value }}
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.usage-stat-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.usage-stat-card__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.usage-stat-card__value {
  margin-top: 6px;
  font-size: 24px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.usage-stat-card__value.is-placeholder {
  color: var(--el-text-color-placeholder);
}
</style>
