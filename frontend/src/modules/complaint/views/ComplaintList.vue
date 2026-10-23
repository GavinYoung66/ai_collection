/**
 * ComplaintList：投诉列表页（tasks 2.2 + 4.1 + 4.2）。
 *
 * 关键约定：
 * - 状态筛选枚举从 types.ts 取（前端禁止发明状态）
 * - onMounted 拉一次 + onActivated 也拉一次——keepAlive 双实例场景（列表页 vs 复核工作台各一份）
 *   各自 onActivated 时刷新，防止 stale data（两个实例互相独立，各自 fetch 一次）
 * - 建单弹窗与列表在同一组件内，建单成功后直接调组件内 fetch() 刷新，不需要 defineExpose
 * - 演示入口 `v-if="import.meta.env.DEV"`——生产构建不渲染、测试用 mock 断言
 * - 不实现 pending 自动研判（已废弃，保留行内"研判"按钮跳转详情）
 * - 统计区只调 GET /complaints/stats，后端返回字段逐字呈现，不自行聚合计算；
 *   基础组件呈现，MUST NOT 引入 ECharts（tasks 4.1 + spec §T-stats）
 * - 运行模式切换入口已下线（灰度运维能力，普通使用者易误解为 AI Agent 模式）；后端接口保留
 */
<script setup lang="ts">
import { ref, onMounted, onActivated, computed } from 'vue'
import { useRouter } from 'vue-router'
import PageTable from '@/shared/components/base/PageTable.vue'
import type { Column } from '@/shared/components/base/PageTable.vue'
import {
  listComplaints,
  createComplaint,
  seedDemoComplaints,
  listComplaintEvents,
  getComplaintStats,
} from '../api'
import type {
  ComplaintListItem,
  TaskStatus,
  ComplaintCreateRequest,
  StatsResponse,
} from '../types'
import { VERDICT_LABELS, STATUS_LABELS } from '../types'
import type { VerdictType } from '../types'

/** 状态筛选下拉选项（顺序同后端 TaskStatus 定义） */
const STATUS_OPTIONS: readonly TaskStatus[] = [
  'pending', 'evidence_gathering', 'reflection_retry', 'judging', 'draft_generated',
  'pending_review', 'pending_senior_review', 'pending_callback', 'resolved', 'closed', 'archived',
]

const router = useRouter()

// ── 状态 ──
defineOptions({ name: 'ComplaintList' })

// import.meta.env.DEV 在 vitest CommonJS 下无法直接出现在 template 里——
// 在 script setup 里赋值给变量（define 能替换），template 引用这个变量
const isDev = import.meta.env.DEV

const loading = ref(false)
const error = ref<Error | null>(null)
const rows = ref<ComplaintListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 状态筛选（单选；复核工作台已改为独立分栏页，不再复用本组件锁定筛选）
const statusFilter = ref<TaskStatus | ''>('')
const effectiveStatusParams = computed<TaskStatus[] | undefined>(() =>
  statusFilter.value ? [statusFilter.value] : undefined,
)

// 建单弹窗状态
const createDialogOpen = ref(false)
const createSubmitting = ref(false)
const createForm = ref<ComplaintCreateRequest>({ complaint_text: '' })

// ── 4.1 统计区状态 ──
const stats = ref<StatsResponse | null>(null)
const statsLoading = ref(false)
const statsError = ref<Error | null>(null)

/** VerdictType → 中文标签映射统一由 types.ts 提供（VERDICT_LABELS） */

// ── 请求 ──
async function fetchList() {
  loading.value = true
  error.value = null
  try {
    const res = await listComplaints({
      page: page.value,
      page_size: pageSize.value,
      task_status: effectiveStatusParams.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

/** 4.1 拉统计——onMounted 时调用，后端字段逐字呈现，不自行聚合 */
async function fetchStats() {
  statsLoading.value = true
  statsError.value = null
  try {
    stats.value = await getComplaintStats()
  } catch (e) {
    statsError.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    statsLoading.value = false
  }
}

// ── 生命周期：列表 + 统计各拉一次（keepAlive 双实例各自刷新）──
onMounted(() => { fetchList(); fetchStats() })
onActivated(() => { fetchList(); fetchStats() })

// ── 事件处理 ──
function onPageChange(p: number) {
  page.value = p
  fetchList()
}
function onPageSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  fetchList()
}
function onRetry() {
  fetchList()
  fetchStats()
}
async function submitCreate() {
  if (!createForm.value.complaint_text.trim()) return
  createSubmitting.value = true
  try {
    await createComplaint(createForm.value)
    createDialogOpen.value = false
    createForm.value.complaint_text = ''
    fetchList()
    fetchStats() // 建单后统计也刷新
  } finally {
    createSubmitting.value = false
  }
}
async function seedDemo() {
  await seedDemoComplaints()
  fetchList()
  fetchStats()
}
async function viewEvents() {
  await listComplaintEvents()
  fetchList()
}

/** 行内入口跳转：带 focus 锚点，详情页据此定位并高亮对应操作区块 */
function openRow(row: ComplaintListItem, focus: 'judge' | 'review' | 'view'): void {
  void router.push({ path: `/complaints/${row.complaint_id}`, query: { focus } })
}

// ── 列定义：操作列由 PageTable #table-row-extra slot 渲染（render 只允许返回原始值）──
const columns: Column<ComplaintListItem>[] = [
  { key: 'complaint_id', label: '工单 ID', width: 200 },
  { key: 'complaint_text', label: '投诉正文', render: (row) => (row.complaint_text.length > 30 ? row.complaint_text.slice(0, 30) + '…' : row.complaint_text) },
  { key: 'task_status', label: '状态', width: 140 },
  { key: 'verdict', label: '研判结论', width: 160, render: (row) => (row.verdict ? VERDICT_LABELS[row.verdict as VerdictType] : '—') },
  { key: 'created_at', label: '创建时间', width: 180 },
]
</script>

<template>
  <div class="complaint-list">
    <div class="cl-header">
      <h2 class="cl-title">
        投诉工单
      </h2>
      <div class="cl-tools">
        <!-- 状态筛选 -->
        <select
          v-model="statusFilter"
          class="cl-filter"
          aria-label="状态筛选"
          @change="fetchList"
        >
          <option value="">
            全部状态
          </option>
          <option
            v-for="s in STATUS_OPTIONS"
            :key="s"
            :value="s"
          >
            {{ STATUS_LABELS[s] }}
          </option>
        </select>

        <!-- 建单按钮 -->
        <button
          type="button"
          class="cl-btn"
          @click="createDialogOpen = true"
        >
          + 手工建单
        </button>

        <!-- 运行模式切换入口已下线：该开关是运营/技术负责人的灰度运维能力（UTF-8 双签），
             对普通使用者无意义且易误解为「AI Agent 模式」。后端 PUT /complaints/mode 保留。 -->

        <!-- DEV 门控：演示入口（DEV=true 才渲染） -->
        <template v-if="isDev">
          <button
            type="button"
            class="cl-btn cl-btn--ghost"
            @click="seedDemo"
          >
            演示灌数
          </button>
          <button
            type="button"
            class="cl-btn cl-btn--ghost"
            @click="viewEvents"
          >
            事件列表
          </button>
        </template>
      </div>
    </div>

    <!-- 4.1 统计卡片区（PageTable 上方） -->
    <section class="cl-stats" aria-label="统计概览">
      <div v-if="statsLoading" class="cl-stats-loading">加载统计中…</div>
      <div v-else-if="statsError" class="cl-stats-error">
        统计加载失败：{{ statsError.message }}
        <button type="button" class="cl-btn" @click="fetchStats">重试</button>
      </div>
      <template v-else-if="stats">
        <div class="cl-stats-row">
          <div class="cl-stat-card" data-stat="total">
            <div class="cl-stat-label">工单总数</div>
            <div class="cl-stat-value">{{ stats.total }}</div>
          </div>
          <div class="cl-stat-card" data-stat="avg_processing_minutes">
            <div class="cl-stat-label">平均处理时长（分钟）</div>
            <div class="cl-stat-value">{{ stats.avg_processing_minutes }}</div>
          </div>
          <div class="cl-stat-card" data-stat="evidence_insufficient_rate">
            <div class="cl-stat-label">证据不足率</div>
            <div class="cl-stat-value">{{ (stats.evidence_insufficient_rate * 100).toFixed(1) }}%</div>
          </div>
          <div class="cl-stat-card" data-stat="mcp_failure_rate">
            <div class="cl-stat-label">MCP 调用失败率</div>
            <div class="cl-stat-value">{{ (stats.mcp_failure_rate * 100).toFixed(1) }}%</div>
          </div>
          <div class="cl-stat-card" data-stat="ai_human_consistency_rate">
            <div class="cl-stat-label">AI-人审一致率</div>
            <div class="cl-stat-value">{{ (stats.ai_human_consistency_rate * 100).toFixed(1) }}%</div>
          </div>
        </div>
        <div class="cl-stats-by-verdict">
          <span class="cl-stat-subtitle">按研判结论分布：</span>
          <div class="cl-by-verdict-list">
            <span
              v-for="(count, verdictKey) in stats.by_verdict"
              :key="verdictKey"
              class="cl-verdict-chip"
            >
              {{ VERDICT_LABELS[verdictKey as VerdictType] ?? verdictKey }}: {{ count }}
            </span>
          </div>
        </div>
      </template>
    </section>

    <!-- 表格体 + 分页：操作列由 PageTable #table-head-extra / #table-row-extra slot 渲染 -->
    <PageTable
      :columns="columns as Column<unknown>[]"
      :rows="rows as unknown[]"
      :total="total"
      :loading="loading"
      :error="error"
      :page="page"
      :page-size="pageSize"
      row-key="complaint_id"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
      @retry="onRetry"
    >
      <template #table-head-extra>
        <th class="pt-th" style="width: 160px">
          操作
        </th>
      </template>
      <template #table-row-extra="{ row }">
        <td class="pt-td" style="width: 160px">
          <div style="display: flex; gap: 8px">
            <!-- 主入口：研判（进详情并定位到研判操作区） -->
            <button
              type="button"
              class="cl-action-btn cl-action-btn--primary"
              @click="openRow(row as ComplaintListItem, 'judge')"
            >
              研判
            </button>
            <!-- 次入口：只读查看，不带锚点 -->
            <button
              type="button"
              class="cl-action-btn"
              @click="openRow(row as ComplaintListItem, 'view')"
            >
              详情
            </button>
          </div>
        </td>
      </template>
    </PageTable>

    <!-- 建单弹窗 -->
    <div
      v-if="createDialogOpen"
      class="cl-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      @click.self="createDialogOpen = false"
    >
      <div class="cl-dialog">
        <h3>手工建单</h3>
        <textarea
          v-model="createForm.complaint_text"
          class="cl-textarea"
          placeholder="请输入投诉正文"
          rows="4"
        />
        <div class="cl-dialog-footer">
          <button
            type="button"
            @click="createDialogOpen = false"
          >
            取消
          </button>
          <button
            type="button"
            class="cl-btn cl-btn--primary"
            :disabled="createSubmitting || !createForm.complaint_text.trim()"
            @click="submitCreate"
          >
            {{ createSubmitting ? '提交中…' : '确认建单' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 运行模式切换弹窗已下线（见工具栏注释） -->
  </div>
</template>

<style scoped>
.complaint-list { padding: 16px 20px; }
.cl-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.cl-title { margin: 0; font-size: 18px; color: #303133; }
.cl-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cl-filter { padding: 4px 8px; border: 1px solid #dcdfe6; border-radius: 4px; font-size: 13px; }
.cl-btn {
  padding: 6px 14px;
  border: 1px solid #dcdfe6;
  background: white;
  color: #606266;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}
.cl-btn--ghost { color: #909399; border-color: #ebeef5; }
.cl-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.cl-btn--mode { border-color: #e6a23c; color: #e6a23c; }
.cl-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cl-mode-result { font-size: 12px; color: #909399; }

/* 4.1 统计卡片区样式 */
.cl-stats {
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafbfc;
}
.cl-stats-loading, .cl-stats-error { font-size: 13px; color: #909399; }
.cl-stats-error { color: #f56c6c; }
.cl-stats-row {
  display: flex; gap: 12px; flex-wrap: wrap;
  margin-bottom: 12px;
}
.cl-stat-card {
  flex: 1; min-width: 140px;
  padding: 12px 16px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  background: white;
}
.cl-stat-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.cl-stat-value { font-size: 20px; font-weight: 600; color: #303133; }
.cl-stat-subtitle { font-size: 13px; color: #606266; margin-right: 8px; }
.cl-by-verdict-list { display: inline-flex; gap: 12px; flex-wrap: wrap; }
.cl-verdict-chip {
  padding: 2px 10px;
  border: 1px solid #dcdfe6;
  border-radius: 12px;
  font-size: 12px;
  color: #606266;
  background: white;
}

/* 4.2 模式切换弹窗 */
.cl-mode-row { display: flex; align-items: flex-start; gap: 8px; flex-direction: column; }
.cl-mode-row label { font-size: 13px; color: #606266; }
.cl-mode-hint { font-size: 11px; color: #e6a23c; }
.cl-required { color: #f56c6c; }

.cl-dialog-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.cl-dialog {
  background: white; border-radius: 8px; padding: 24px;
  width: 480px; display: flex; flex-direction: column; gap: 16px;
}
.cl-dialog h3 { margin: 0; font-size: 16px; }
.cl-textarea {
  width: 100%; padding: 8px; border: 1px solid #dcdfe6; border-radius: 4px;
  resize: vertical; box-sizing: border-box; font-family: inherit; font-size: 14px;
}
.cl-dialog-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
