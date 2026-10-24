<script setup lang="ts">
/**
 * QcAlerts：告警流（f2-qc-console 批次5 / tasks 6.4）。
 *
 * 规格（spec「告警流必须提供列表与确认」）：
 * - 告警列表 GET alerts（alerts.py:26-48，分页 page 从 1、page_size 默认 20）；
 * - 行内「确认」POST alerts/{alert_id}/ack：提交中防连点（在途集合 + 按钮禁用），
 *   成功后按响应 status 就地更新该条状态（不整表刷新）；
 * - 已确认行（status === 'ACKED'，以后端字段为准）禁用确认按钮；
 * - keepAlive：onMounted 拉一次，onActivated 刷新（首个激活去重，同其他列表页）。
 */
import { onActivated, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import PageTable from '@/shared/components/base/PageTable.vue'
import type { Column } from '@/shared/components/base/PageTable.vue'
import { ackQcAlert, listQcAlerts } from '../api'
import type { QcAlertItem, QcAlertStatus } from '../types'

defineOptions({ name: 'QcAlerts' })

// ── 展示文案映射（状态值以 types.ts 枚举为准，前端不发明状态）──
const ALERT_STATUS_LABELS: Record<QcAlertStatus, string> = {
  NEW: '未确认',
  ACKED: '已确认',
}

// ── 列表状态 ──
const rows = ref<QcAlertItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref<Error | null>(null)

// ── 确认在途集合（防连点）──
const ackingIds = ref<string[]>([])

async function fetchAlerts(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listQcAlerts({ page: page.value, page_size: pageSize.value })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchAlerts()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchAlerts()
})

function onPageChange(p: number): void {
  page.value = p
  void fetchAlerts()
}
function onPageSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  void fetchAlerts()
}
function onRetry(): void {
  void fetchAlerts()
}

/** 告警确认：POST alerts/{alert_id}/ack；提交中防连点，成功后就地更新该条状态 */
async function onAck(row: QcAlertItem): Promise<void> {
  if (row.status === 'ACKED') return
  if (ackingIds.value.includes(row.alert_id)) return
  ackingIds.value = [...ackingIds.value, row.alert_id]
  try {
    const res = await ackQcAlert(row.alert_id)
    // 就地更新该条状态（以响应 status 为准），不整表刷新
    rows.value = rows.value.map((item) =>
      item.alert_id === row.alert_id ? { ...item, status: res.status } : item,
    )
    ElMessage.success('告警已确认')
  } catch (e) {
    ElMessage.error(`告警确认失败：${(e as Error).message}`)
  } finally {
    ackingIds.value = ackingIds.value.filter((id) => id !== row.alert_id)
  }
}

// ── 列定义：操作列走 #table-row-extra slot ──
const columns: Column<QcAlertItem>[] = [
  { key: 'alert_id', label: '告警 ID', width: 200 },
  { key: 'session_id', label: '会话 ID', width: 200, render: (row) => row.session_id ?? '—' },
  { key: 'level', label: '级别', width: 70 },
  {
    key: 'status',
    label: '状态',
    width: 90,
    render: (row) => ALERT_STATUS_LABELS[row.status],
  },
  { key: 'evidence', label: '证据', render: (row) => row.evidence || '—' },
  { key: 'create_time', label: '告警时间', width: 190, render: (row) => row.create_time ?? '—' },
]
</script>

<template>
  <div class="qc-alerts">
    <header class="qal-header">
      <h2 class="qal-title">
        告警流
      </h2>
      <button
        type="button"
        class="qal-btn"
        :disabled="loading"
        @click="onRetry"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <PageTable
      :columns="columns as Column<unknown>[]"
      :rows="rows as unknown[]"
      :total="total"
      :loading="loading"
      :error="error"
      :page="page"
      :page-size="pageSize"
      row-key="alert_id"
      empty-text="暂无告警"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
      @retry="onRetry"
    >
      <template #table-head-extra>
        <th
          class="pt-th"
          style="width: 100px"
        >
          操作
        </th>
      </template>
      <template #table-row-extra="{ row }">
        <td
          class="pt-td"
          style="width: 100px"
        >
          <!-- 已确认行禁用确认按钮（以后端 status 为准）；提交中防连点 -->
          <button
            type="button"
            class="qal-action"
            :disabled="(row as QcAlertItem).status === 'ACKED' || ackingIds.includes((row as QcAlertItem).alert_id)"
            @click="onAck(row as QcAlertItem)"
          >
            {{ (row as QcAlertItem).status === 'ACKED' ? '已确认' : (ackingIds.includes((row as QcAlertItem).alert_id) ? '确认中…' : '确认') }}
          </button>
        </td>
      </template>
    </PageTable>
  </div>
</template>

<style scoped>
.qc-alerts { padding: 16px 20px; }
.qal-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.qal-title { margin: 0; font-size: 18px; color: #303133; }
.qal-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qal-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.qal-action {
  padding: 4px 12px; border: 1px solid #409eff; background: #409eff; color: white;
  border-radius: 4px; cursor: pointer; font-size: 12px;
}
.qal-action:disabled { opacity: 0.5; cursor: not-allowed; border-color: #dcdfe6; background: #f5f7fa; color: #909399; }
</style>
