<script setup lang="ts">
/**
 * 调用明细表（f3-usage-console 批次⑥ / tasks 6.1-6.4）。
 *
 * - 分页：默认 20、可选 20/50/100（上限由 api.ts 再钳一次）；
 * - 筛选：status 草案四值（`llm_log.py:25`）+「全部」；筛选变更重置到第 1 页；
 * - trace_id 精确检索：提交值作为 `trace_id` 参数，空串不拼参数；
 * - status 逐字展示后端原值，不做映射（spec R5）；tag 颜色仅视觉区分；
 * - 无结果以 `total === 0` 判定为页内空态，不弹全局错误；
 * - error_msg 走行展开，展示原文与完整 trace_id。
 */
import { ref, watch } from 'vue'
import { TraceIdText, useTableQuery } from '@/shared'
import { listUsageLogs } from '../api'
import type { UsageLogItem, UsageLogStatus, UsageTimeRange } from '../types'

const props = defineProps<{
  range: UsageTimeRange
}>()

// 表格三态 / 分页 / 筛选统一由 shared 维护（SSOT §19.4）
const {
  rows,
  total,
  page,
  pageSize,
  query,
  loading,
  error,
  reload: fetchLogs,
  setPage: onPageChange,
  setPageSize: onPageSizeChange,
} = useTableQuery<UsageLogItem, { status: UsageLogStatus | ''; trace: string }>({
  fetcher: (q, p, ps) =>
    listUsageLogs({
      start: props.range.start,
      end: props.range.end,
      page: p,
      page_size: ps,
      ...(q.status ? { status: q.status } : {}),
      ...(q.trace ? { trace_id: q.trace } : {}),
    }),
  initialQuery: { status: '', trace: '' },
  pageSize: 20,
})

const traceInput = ref('')

/** 筛选项：实际写入值（llm_reviewer.py 写入的 ok / qc_review_exemption / skipped）在前，
 *  计划值（llm_log.py 列注释 success/failed/timeout/circuit_open）保留为快捷项。
 *  前端不改写状态，筛选值原样透传后端（spec R5）。 */
const STATUS_OPTIONS: Array<{ value: UsageLogStatus; label: string }> = [
  { value: 'ok', label: '成功（ok）' },
  { value: 'qc_review_exemption', label: '质检复核豁免' },
  { value: 'skipped', label: '跳过（skipped）' },
  { value: 'failed', label: '失败（failed）' },
  { value: 'timeout', label: '超时（timeout）' },
  { value: 'circuit_open', label: '熔断（circuit_open）' },
  { value: 'success', label: '成功（success，计划值）' },
]

/** 仅决定 tag 颜色，不改变展示文案（原值直出） */
function statusTagType(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'ok' || value === 'success') return 'success'
  if (value === 'failed' || value === 'timeout' || value === 'circuit_open') return 'danger'
  return 'info'
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function formatTokens(item: UsageLogItem): string {
  const value = item.token_usage?.total_tokens
  return typeof value === 'number' ? value.toLocaleString() : '—'
}

function formatLatency(item: UsageLogItem): string {
  return typeof item.latency_ms === 'number' ? `${Math.round(item.latency_ms)} ms` : '—'
}

/** 时间范围变化（父级重新取数）→ 回到第 1 页 */
watch(
  () => [props.range.start, props.range.end],
  () => {
    void onPageChange(1)
  },
)

/** trace_id 精确检索：只认提交值，输入过程不请求 */
function submitTrace(): void {
  const next = traceInput.value.trim()
  if (next === query.value.trace) {
    void fetchLogs() // 同值重复提交也要重查
    return
  }
  // 赋值触发 query 深度 watch：自动回到第 1 页并重查
  query.value.trace = next
}

function resetTrace(): void {
  traceInput.value = ''
  if (query.value.trace === '') {
    void fetchLogs()
    return
  }
  query.value.trace = ''
}
</script>

<template>
  <div class="usage-log-table">
    <div class="usage-log-filters">
      <el-select
        v-model="query.status"
        size="small"
        placeholder="全部状态"
        class="usage-log-filters__status"
      >
        <el-option
          label="全部状态"
          value=""
        />
        <el-option
          v-for="opt in STATUS_OPTIONS"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <el-input
        v-model="traceInput"
        size="small"
        placeholder="trace_id 精确检索"
        clearable
        class="usage-log-filters__trace"
        @keyup.enter="submitTrace"
        @clear="resetTrace"
      />
      <el-button
        size="small"
        type="primary"
        @click="submitTrace"
      >
        检索
      </el-button>
    </div>

    <el-alert
      v-if="error"
      class="usage-error"
      :title="error?.message ?? ''"
      type="error"
      :closable="false"
      show-icon
    />

    <el-empty
      v-if="!loading && total === 0"
      :description="query.trace ? '未找到该 trace_id 的调用记录' : '所选时间范围内暂无调用明细'"
    />
    <el-table
      v-else
      v-loading="loading"
      :data="rows"
      size="small"
      border
    >
      <el-table-column
        type="expand"
        width="40"
      >
        <template #default="{ row }">
          <div class="usage-log-expand">
            <div class="usage-log-expand__row">
              <span class="usage-log-expand__label">trace_id</span>
              <TraceIdText :trace-id="row.trace_id" />
            </div>
            <div class="usage-log-expand__row">
              <span class="usage-log-expand__label">error_msg</span>
              <pre class="usage-log-expand__text">{{ row.error_msg || '无错误信息' }}</pre>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="trace_id"
        min-width="120"
      >
        <template #default="{ row }">
          <TraceIdText :trace-id="row.trace_id" />
        </template>
      </el-table-column>
      <el-table-column
        prop="model"
        label="model"
        min-width="120"
      />
      <el-table-column
        label="status"
        width="160"
      >
        <template #default="{ row }">
          <el-tag
            :type="statusTagType(row.status)"
            size="small"
          >
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="latency"
        width="100"
      >
        <template #default="{ row }">
          {{ formatLatency(row) }}
        </template>
      </el-table-column>
      <el-table-column
        label="token"
        width="110"
      >
        <template #default="{ row }">
          {{ formatTokens(row) }}
        </template>
      </el-table-column>
      <el-table-column
        label="create_time"
        min-width="160"
      >
        <template #default="{ row }">
          {{ formatTime(row.create_time) }}
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > 0"
      class="usage-log-pagination"
      :current-page="page"
      :page-size="pageSize"
      :page-sizes="[20, 50, 100]"
      :total="total"
      layout="total, sizes, prev, pager, next"
      size="small"
      @current-change="onPageChange"
      @size-change="onPageSizeChange"
    />
  </div>
</template>

<style scoped>
.usage-log-table {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.usage-log-filters {
  display: flex;
  align-items: center;
  gap: 8px;
}

.usage-log-filters__status {
  width: 160px;
}

.usage-log-filters__trace {
  width: 280px;
}

.usage-log-expand {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px;
}

.usage-log-expand__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.usage-log-expand__label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  width: 72px;
  flex: none;
}

.usage-log-expand__text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: monospace;
  font-size: 12px;
}

.usage-log-pagination {
  justify-content: flex-end;
}
</style>
