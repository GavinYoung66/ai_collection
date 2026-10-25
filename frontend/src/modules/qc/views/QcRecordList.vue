<script setup lang="ts">
/**
 * QcRecordList：质检报告台账（f2-qc-console 批次4 / tasks 5.1）。
 *
 * 规格（spec「报告列表必须以扣分列呈现并提供统计卡」）：
 * - PageTable 分页请求（page 从 1、page_size 默认 20、上限 100，api 层钳制）；
 * - 台账以「扣分」列呈现 deduction_total，不展示 grade 列与合规分列（grade 已退役恒 null）；
 * - 统计卡基于 GET records/stats 返回字段逐字呈现（key/count/high_count、from/to 窗口），
 *   前端不自行聚合，不引入 ECharts；
 * - 行点击 / 操作列按钮跳详情 /qc/records/:sessionId；
 * - keepAlive：onMounted 拉一次，onActivated 刷新（首个 activated 与 mounted 同序，标志位去重）。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { PageTable, useAsyncTask, useTableQuery } from '@/shared'
import type { Column } from '@/shared'
import { getQcRecordStats, listQcRecords } from '../api'
import type {
  QcRecordListItem,
  QcRiskLevel,
  QcStatsGroupBy,
  QcStatus,
} from '../types'

defineOptions({ name: 'QcRecordList' })

const router = useRouter()

// ── 展示文案映射（状态值以 types.ts 枚举为准，前端不发明状态）──
const QC_STATUS_LABELS: Record<QcStatus, string> = {
  DONE: '已完成',
  PENDING_REVIEW: '待复核',
  REVIEWED: '已人审',
  PARTIAL: '部分完成',
}
const RISK_LABELS: Record<QcRiskLevel, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

// ── 列表三态 / 分页统一由 shared 维护（SSOT §19.4）──
const {
  rows,
  total,
  page,
  pageSize,
  loading,
  error,
  reload: fetchList,
  setPage: onPageChange,
  setPageSize: onPageSizeChange,
} = useTableQuery<QcRecordListItem, Record<string, never>>({
  fetcher: (_q, p, ps) => listQcRecords({ page: p, page_size: ps }),
  initialQuery: {},
  pageSize: 20,
  refetchOnActivated: true,
})

// ── 统计卡（records/stats；group_by 必填，默认按日维度）──
const statsGroupBy = ref<QcStatsGroupBy>('day')
const {
  data: stats,
  loading: statsLoading,
  error: statsError,
  run: fetchStats,
} = useAsyncTask(() => getQcRecordStats({ group_by: statsGroupBy.value }), {
  refetchOnActivated: true,
})

const STATS_GROUP_OPTIONS: { value: QcStatsGroupBy; label: string }[] = [
  { value: 'day', label: '按日' },
  { value: 'agent', label: '按坐席' },
  { value: 'rule_id', label: '按规则' },
]

function onRetry(): void {
  void fetchList()
  void fetchStats()
}
function onStatsGroupChange(): void {
  void fetchStats()
}

function openRecord(row: QcRecordListItem): void {
  void router.push(`/qc/records/${row.session_id}`)
}

/** 行点击跳详情（事件委托：PageTable 行 class 固定为 pt-tr，按 tbody 内序号映射行数据） */
function onTableClick(e: MouseEvent): void {
  const tr = (e.target as HTMLElement).closest('tr.pt-tr')
  if (!tr || !tr.parentElement) return
  const idx = Array.from(tr.parentElement.children).indexOf(tr)
  const row = rows.value[idx]
  if (row) openRecord(row)
}

// ── 列定义：扣分列替代合规分，无 grade 列；操作列走 #table-row-extra slot ──
const columns: Column<QcRecordListItem>[] = [
  { key: 'session_id', label: '会话 ID', width: 220 },
  { key: 'case_id', label: '案件 ID', width: 160, render: (row) => row.case_id ?? '—' },
  { key: 'deduction_total', label: '扣分', width: 100, align: 'right', render: (row) => `-${row.deduction_total}` },
  { key: 'risk', label: '风险', width: 80, render: (row) => RISK_LABELS[row.risk] },
  { key: 'qc_status', label: '质检状态', width: 110, render: (row) => QC_STATUS_LABELS[row.qc_status] },
  { key: 'create_time', label: '质检时间', width: 190, render: (row) => row.create_time ?? '—' },
]
</script>

<template>
  <div class="qc-record-list">
    <header class="qrl-header">
      <h2 class="qrl-title">
        质检报告
      </h2>
      <div class="qrl-tools">
        <label class="qrl-stat-filter">
          统计维度
          <select
            v-model="statsGroupBy"
            aria-label="统计维度"
            @change="onStatsGroupChange"
          >
            <option
              v-for="opt in STATS_GROUP_OPTIONS"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </option>
          </select>
        </label>
        <button
          type="button"
          class="qrl-btn"
          :disabled="loading"
          @click="onRetry"
        >
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- 统计卡片区（表上方；records/stats 字段透传，不聚合、不引图表库） -->
    <section
      class="qrl-stats"
      aria-label="统计概览"
    >
      <div
        v-if="statsLoading"
        class="qrl-stats-hint"
      >
        加载统计中…
      </div>
      <div
        v-else-if="statsError"
        class="qrl-stats-hint qrl-stats-hint--error"
      >
        统计加载失败：{{ statsError.message }}
        <button
          type="button"
          class="qrl-btn"
          @click="fetchStats"
        >
          重试
        </button>
      </div>
      <template v-else-if="stats">
        <div class="qrl-stats-row">
          <div
            v-for="item in stats.items"
            :key="item.key"
            class="qrl-stat-card"
            :data-stat-key="item.key"
          >
            <div class="qrl-stat-label">
              {{ item.key }}
            </div>
            <div class="qrl-stat-value">
              {{ item.count }}
            </div>
            <div
              v-if="item.high_count !== undefined"
              class="qrl-stat-sub"
            >
              高风险 {{ item.high_count }}
            </div>
          </div>
          <p
            v-if="!stats.items.length"
            class="qrl-stats-hint"
          >
            暂无统计数据
          </p>
        </div>
        <p
          v-if="stats.from || stats.to"
          class="qrl-stats-window"
        >
          统计窗口：{{ stats.from ?? '—' }} ~ {{ stats.to ?? '—' }}（{{ stats.group_by }}）
        </p>
      </template>
    </section>

    <!-- 台账表（行点击跳详情；扣分列替代合规分，无 grade 列） -->
    <div @click="onTableClick">
      <PageTable
        :columns="columns as Column<unknown>[]"
        :rows="rows as unknown[]"
        :total="total"
        :loading="loading"
        :error="error"
        :page="page"
        :page-size="pageSize"
        row-key="qc_record_id"
        empty-text="暂无质检记录"
        @update:page="onPageChange"
        @update:page-size="onPageSizeChange"
        @retry="onRetry"
      >
        <template #table-head-extra>
          <th
            class="pt-th"
            style="width: 110px"
          >
            操作
          </th>
        </template>
        <template #table-row-extra="{ row }">
          <td
            class="pt-td"
            style="width: 110px"
          >
            <button
              type="button"
              class="qrl-action"
              @click.stop="openRecord(row as QcRecordListItem)"
            >
              查看详情
            </button>
          </td>
        </template>
      </PageTable>
    </div>
  </div>
</template>

<style scoped>
.qc-record-list { padding: 16px 20px; }
.qrl-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.qrl-title { margin: 0; font-size: 18px; color: #303133; }
.qrl-tools { display: flex; align-items: center; gap: 8px; }
.qrl-stat-filter { font-size: 13px; color: #606266; display: flex; align-items: center; gap: 6px; }
.qrl-stat-filter select { padding: 4px 8px; border: 1px solid #dcdfe6; border-radius: 4px; font-size: 13px; }
.qrl-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qrl-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.qrl-stats {
  margin-bottom: 16px; padding: 16px;
  border: 1px solid #ebeef5; border-radius: 6px; background: #fafbfc;
}
.qrl-stats-hint { font-size: 13px; color: #909399; margin: 0; }
.qrl-stats-hint--error { color: #f56c6c; }
.qrl-stats-row { display: flex; gap: 12px; flex-wrap: wrap; }
.qrl-stat-card {
  flex: 1; min-width: 140px; padding: 12px 16px;
  border: 1px solid #ebeef5; border-radius: 4px; background: white;
}
.qrl-stat-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.qrl-stat-value { font-size: 20px; font-weight: 600; color: #303133; }
.qrl-stat-sub { font-size: 12px; color: #f56c6c; margin-top: 4px; }
.qrl-stats-window { margin: 10px 0 0; font-size: 12px; color: #909399; }

.qrl-action {
  padding: 4px 12px; border: 1px solid #409eff; background: white; color: #409eff;
  border-radius: 4px; cursor: pointer; font-size: 12px;
}
</style>
