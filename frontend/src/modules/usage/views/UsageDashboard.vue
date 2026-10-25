<script setup lang="ts">
/**
 * LLM 用量看板（f3-usage-console 批次④ / tasks 4.2-4.3）。
 *
 * - 时间范围：近 1 天 / 7 天 / 30 天 / 自定义；组件本地 ref 管状态（design D5），
 *   切换即重新取数，自定义未选完整不请求；
 * - 空态：`totals.calls === 0` 展示引导文案，不渲染零值图表（spec R3）；
 * - keepAlive：首个 onActivated 与 onMounted 同序触发，标志位去重，避免首屏双取
 *   （照 QcRules/KbSensitive 先例）；缓存返回时刷新；
 * - 本批只读、无写操作入口（spec R9）。
 */
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { getUsageSummary } from '../api'
import UsageStatCards from '../components/UsageStatCards.vue'
import UsageModelChart from '../components/UsageModelChart.vue'
import UsageLogTable from '../components/UsageLogTable.vue'
import UsageServiceChart from '../components/UsageServiceChart.vue'
import UsageTrendChart from '../components/UsageTrendChart.vue'
import type {
  UsageSummaryResponse,
  UsageTimeRange,
  UsageTimeRangeKey,
} from '../types'

defineOptions({ name: 'UsageDashboard' })

const rangeKey = ref<UsageTimeRangeKey>('7d')
const customRange = ref<string[] | null>(null)
const activeTab = ref<'dashboard' | 'logs'>('dashboard')
const summary = ref<UsageSummaryResponse | null>(null)
/** 看板实际取数用的范围，明细 Tab 复用同一窗口（design D2：同页 Tab 共享筛选） */
const activeRange = ref<UsageTimeRange | null>(null)
const loading = ref(false)
const errorMsg = ref<string | null>(null)

/** 看板空态判据：仅在拿到响应后判定（spec R3） */
const isEmpty = computed(
  () => summary.value !== null && summary.value.totals.calls === 0,
)

/** 快捷范围 → [start,end]（ISO 8601）；自定义未选完返回 null，不发起请求 */
function resolveRange(): UsageTimeRange | null {
  if (rangeKey.value === 'custom') {
    if (!customRange.value || customRange.value.length !== 2) return null
    return {
      start: new Date(customRange.value[0]!).toISOString(),
      end: new Date(customRange.value[1]!).toISOString(),
    }
  }
  const end = new Date()
  const days = rangeKey.value === '1d' ? 1 : rangeKey.value === '30d' ? 30 : 7
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000)
  return { start: start.toISOString(), end: end.toISOString() }
}

async function fetchSummary(): Promise<void> {
  const range = resolveRange()
  if (!range) return
  loading.value = true
  errorMsg.value = null
  try {
    const result = await getUsageSummary(range)
    summary.value = result
    activeRange.value = range
  } catch (e) {
    summary.value = null
    errorMsg.value = e instanceof Error ? e.message : '用量数据加载失败'
  } finally {
    loading.value = false
  }
}

/** keepAlive：首个 activated 与 mounted 同序触发，标志位去重（同 QcRules 先例） */
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchSummary()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchSummary()
})

/** 快捷范围切换即重取；切到「自定义」但尚未选完不请求 */
watch(rangeKey, (key) => {
  if (key === 'custom' && (!customRange.value || customRange.value.length !== 2)) return
  void fetchSummary()
})

/** 自定义范围选完立即重取（spec R3「切换触发重新取数」） */
function onCustomRangeChange(value: string[] | null): void {
  customRange.value = value
  if (value && value.length === 2) void fetchSummary()
}
</script>

<template>
  <div
    v-loading="loading"
    class="usage-dashboard"
  >
    <header class="usage-header">
      <h2 class="usage-title">
        LLM 用量
      </h2>
      <div class="usage-tools">
        <el-radio-group
          v-model="rangeKey"
          size="small"
        >
          <el-radio-button value="1d">
            近 1 天
          </el-radio-button>
          <el-radio-button value="7d">
            近 7 天
          </el-radio-button>
          <el-radio-button value="30d">
            近 30 天
          </el-radio-button>
          <el-radio-button value="custom">
            自定义
          </el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="rangeKey === 'custom'"
          :model-value="customRange"
          type="datetimerange"
          value-format="YYYY-MM-DDTHH:mm:ss"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          size="small"
          @update:model-value="onCustomRangeChange"
        />
      </div>
    </header>

    <el-alert
      v-if="errorMsg"
      class="usage-error"
      :title="errorMsg"
      type="error"
      :closable="false"
      show-icon
    />
    <div
      v-if="errorMsg"
      class="usage-error-actions"
    >
      <el-button
        size="small"
        @click="fetchSummary"
      >
        重试
      </el-button>
    </div>

    <!-- 常驻横幅：数据完整性缺口与后端状态无关，恒定展示（design D7） -->
    <el-alert
      class="usage-banner"
      title="用量数据当前仅 qc-service 上报；complaint/rag 埋点与 token/时延字段补齐前，分布与均值可能不完整"
      type="info"
      :closable="false"
      show-icon
    />

    <el-tabs
      v-model="activeTab"
      class="usage-tabs"
    >
      <el-tab-pane
        label="看板"
        name="dashboard"
      >
        <el-empty
          v-if="isEmpty"
          description="所选时间范围内暂无调用记录"
        />
        <template v-else-if="summary">
          <UsageStatCards :totals="summary.totals" />
          <div class="usage-charts">
            <UsageTrendChart
              class="usage-charts__wide"
              :data="summary?.trend ?? []"
            />
            <UsageModelChart :data="summary?.by_model ?? []" />
            <UsageServiceChart
              v-if="summary?.by_service?.length"
              :data="summary.by_service"
            />
            <!-- 韧性三项：缺失整块隐藏，前端不自算（design D4） -->
            <el-card
              v-if="summary?.resilience"
              class="usage-resilience-card"
              shadow="never"
            >
              <template #header>
                <span>韧性降级</span>
              </template>
              <div class="usage-resilience">
                <div class="usage-resilience__item">
                  <span class="usage-resilience__label">限流</span>
                  <span class="usage-resilience__value">{{ summary.resilience.rate_limited }}</span>
                </div>
                <div class="usage-resilience__item">
                  <span class="usage-resilience__label">熔断</span>
                  <span class="usage-resilience__value">{{ summary.resilience.breaker_open }}</span>
                </div>
                <div class="usage-resilience__item">
                  <span class="usage-resilience__label">降级</span>
                  <span class="usage-resilience__value">{{ summary.resilience.fallback }}</span>
                </div>
              </div>
            </el-card>
          </div>
        </template>
      </el-tab-pane>

      <el-tab-pane
        label="调用明细"
        name="logs"
        lazy
      >
        <UsageLogTable
          v-if="activeRange"
          :range="activeRange"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.usage-dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.usage-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.usage-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.usage-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.usage-error-actions {
  display: flex;
  justify-content: flex-end;
}

.usage-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.usage-charts__wide {
  grid-column: span 2;
}

.usage-resilience {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.usage-resilience__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.usage-resilience__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.usage-resilience__value {
  font-size: 20px;
  font-weight: 600;
}
</style>
