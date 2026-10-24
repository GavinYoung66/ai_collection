<script setup lang="ts">
/**
 * QcReview：人工复核待复核队列（f2-qc-console 批次5 / tasks 6.1）。
 *
 * 规格（spec「人工复核必须按后端枚举与扣分状态呈现」）：
 * - 待复核队列 GET reviews/pending（review.py:40-60，分页 page 从 1、page_size 默认 20）；
 * - 行内嵌人审操作组 ReviewActions（与详情页共用同一组件，props 传入行数据），
 *   零扣分不渲染提交按钮、CONFIRM 直提、CORRECT 必填 revised_deduction、REVIEWED 禁用、
 *   角色 ∉ {qc_reviewer, supervisor} 置灰（组件内自行判定，本页不重复实现）；
 * - 复核提交成功（submitted）后刷新队列；行内提供「查看详情」跳 /qc/records/:sessionId；
 * - keepAlive：onMounted 拉一次，onActivated 刷新（首个 activated 与 mounted 同序，标志位去重）。
 */
import { onActivated, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import PageTable from '@/shared/components/base/PageTable.vue'
import type { Column } from '@/shared/components/base/PageTable.vue'
import { listQcReviewsPending } from '../api'
import type { QcReviewPendingItem, QcResult, QcRiskLevel, QcStatus } from '../types'
import ReviewActions from '../components/ReviewActions.vue'

defineOptions({ name: 'QcReview' })

const router = useRouter()

// ── 展示文案映射（状态值以 types.ts 枚举为准，前端不发明状态）──
const QC_STATUS_LABELS: Record<QcStatus, string> = {
  DONE: '已完成',
  PENDING_REVIEW: '待复核',
  REVIEWED: '已人审',
  PARTIAL: '部分完成',
}
const QC_RESULT_LABELS: Record<QcResult, string> = {
  compliant: '合规',
  violation: '违规',
  pending_review: '待复核',
}
const RISK_LABELS: Record<QcRiskLevel, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

// ── 列表状态 ──
const rows = ref<QcReviewPendingItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref<Error | null>(null)

async function fetchQueue(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listQcReviewsPending({ page: page.value, page_size: pageSize.value })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重（同 QcRecordList 先例）
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchQueue()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchQueue()
})

function onPageChange(p: number): void {
  page.value = p
  void fetchQueue()
}
function onPageSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  void fetchQueue()
}
function onRetry(): void {
  void fetchQueue()
}

function openRecord(row: QcReviewPendingItem): void {
  void router.push(`/qc/records/${row.session_id}`)
}

/** 人审提交成功 → 刷新队列（该条 qc_status 进入 REVIEWED） */
function onSubmitted(): void {
  void fetchQueue()
}

// ── 列定义：扣分列呈现 deduction_total，无 grade 列；操作列走 #table-row-extra slot ──
const columns: Column<QcReviewPendingItem>[] = [
  { key: 'session_id', label: '会话 ID', width: 200 },
  { key: 'case_id', label: '案件 ID', width: 140, render: (row) => row.case_id ?? '—' },
  { key: 'deduction_total', label: '扣分', width: 90, align: 'right', render: (row) => `-${row.deduction_total}` },
  { key: 'risk', label: '风险', width: 70, render: (row) => RISK_LABELS[row.risk] },
  { key: 'qc_result', label: '质检结论', width: 90, render: (row) => QC_RESULT_LABELS[row.qc_result] },
  { key: 'qc_status', label: '质检状态', width: 100, render: (row) => QC_STATUS_LABELS[row.qc_status] },
]
</script>

<template>
  <div class="qc-review">
    <header class="qrv-header">
      <h2 class="qrv-title">
        人工复核
      </h2>
      <button
        type="button"
        class="qrv-btn"
        :disabled="loading"
        @click="onRetry"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <!-- 待复核队列（行内嵌人审操作组；三态由 PageTable 内置） -->
    <PageTable
      :columns="columns as Column<unknown>[]"
      :rows="rows as unknown[]"
      :total="total"
      :loading="loading"
      :error="error"
      :page="page"
      :page-size="pageSize"
      row-key="qc_record_id"
      empty-text="暂无待复核记录"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
      @retry="onRetry"
    >
      <template #table-head-extra>
        <th
          class="pt-th"
          style="width: 380px"
        >
          复核操作
        </th>
      </template>
      <template #table-row-extra="{ row }">
        <td
          class="pt-td"
          style="width: 380px"
        >
          <div class="qrv-row-actions">
            <!-- 人审操作组：零扣分/REVIEWED/越权置灰等形态由组件内部判定 -->
            <ReviewActions
              :qc-record-id="(row as QcReviewPendingItem).qc_record_id"
              :deduction-total="(row as QcReviewPendingItem).deduction_total"
              :qc-status="(row as QcReviewPendingItem).qc_status"
              @submitted="onSubmitted"
            />
            <button
              type="button"
              class="qrv-action"
              @click="openRecord(row as QcReviewPendingItem)"
            >
              查看详情
            </button>
          </div>
        </td>
      </template>
    </PageTable>
  </div>
</template>

<style scoped>
.qc-review { padding: 16px 20px; }
.qrv-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.qrv-title { margin: 0; font-size: 18px; color: #303133; }
.qrv-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qrv-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.qrv-row-actions { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.qrv-action {
  padding: 4px 12px; border: 1px solid #409eff; background: white; color: #409eff;
  border-radius: 4px; cursor: pointer; font-size: 12px;
}
</style>
