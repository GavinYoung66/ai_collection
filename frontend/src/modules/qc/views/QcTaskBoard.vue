<script lang="ts">
/**
 * 批量勾选 UI 上限（design D2）：单条 replay 最坏约 9s × 10 = 90s < TIMEOUT.qcReplay 110s，
 * 留 20s 余量；超出禁用并提示「大批量走后端异步（P1）」。
 * 这是体验约束，不替代后端 QC_BATCH_MAX=100 硬限制（check.py:192）。
 */
export const QC_BATCH_LIMIT = 10
</script>

<script setup lang="ts">
/**
 * QcTaskBoard：质检任务台（f2-qc-console 批次3 / tasks 4.1-4.3）。
 *
 * 合并视图口径（design 风险条「demo/pending 与 records 双数据源」）：
 * - 待检行：GET demo/pending（demo_seed_service.py:95-114，后端已排除有有效记录的会话），
 *   行内提供「开始质检」（POST replay/{session_id}）；
 * - 已出分行：GET records 首页（page=1&page_size=20），仅展示与跳转详情，不自动重跑
 *   （spec「已出分行不重跑」）；与待检集合按 session_id 去重，待检优先；
 * - 全走后端接口，不读写浏览器本地缓存（旧演示页缓存行为属已批准废弃项）。
 *
 * keepAlive 语义：onMounted 拉一次；onActivated 作为缓存激活刷新入口，
 * 首个 activated 与 mounted 同序触发，用标志位去重避免重复拉取。
 *
 * 错误处理（§15.1 白名单模块侧动作，禁止新增映射，design D6）：
 * - A2003 → 该行质检按钮置灰 + 悬浮后端原因，不再发起请求；
 * - A2002 → 原始回放表单行内标红（qc 的 422 不叠 B0001）；
 * - 网关 504/B0009 等 → ElMessage 可解释提示（拦截器已附白名单文案 + trace 后 8 位）；
 * - 其余 A20xx → 默认分支 message 直出。
 */
import { computed, onActivated, onMounted, onUnmounted, ref, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { RequestError } from '@/shared/request'
import { randomUuid } from '@/shared'
import {
  checkQcBatch,
  listQcDemoPending,
  listQcRecords,
  replayQcRaw,
  replayQcSession,
  seedQcDemo,
} from '../api'
import type {
  QcCheckBatchResponse,
  QcDemoPendingItem,
  QcRecordListItem,
  QcReplayRawRequest,
  QcRiskLevel,
  QcStatus,
} from '../types'

defineOptions({ name: 'QcTaskBoard' })

// import.meta.env.DEV 在 vitest 下经 define 静态替换；template 引用变量而非表达式
// （complaint 先例：CommonJS 下 import.meta.env 不能直接出现在 template）
const isDev = import.meta.env.DEV

const router = useRouter()

// 卸载时取消在途长请求（replay/check/batch 同步含 LLM 复核，最坏 ~90s）
const unmountAbort = new AbortController()

// ── 状态标签（前端展示文案，状态值本身以 types.ts 枚举为准，不发明状态）──
const QC_STATUS_LABELS: Record<QcStatus, string> = {
  DONE: '已完成',
  PENDING_REVIEW: '待复核',
  REVIEWED: '已人审',
  PARTIAL: '部分完成',
}
/**
 * 任务台行模型：kind 区分双数据源。
 * pending=待检（可开检/可勾选批量）；scored=已出分（仅查看详情，不重跑）。
 */
interface QcBoardRow {
  session_id: string
  scenario: string | null
  label: string | null
  case_id: string | null
  call_id: string | null
  kind: 'pending' | 'scored'
  qc_status: QcStatus | null
  deduction_total: number | null
  risk: QcRiskLevel | null
}

// ── 列表状态 ──
const pendingItems = ref<QcDemoPendingItem[]>([])
const scoredItems = ref<QcRecordListItem[]>([])
const listLoading = ref(false)
const loadError = ref<Error | null>(null)

// ── 行内开检状态：在途集合（防连点）与 A2003 置灰原因（session_id → 后端 message）──
const replayingIds = ref<string[]>([])
const blockedReasons = ref<Record<string, string>>({})

// ── 批量勾选 ──
const selectedIds = ref<string[]>([])
const batchSubmitting = ref(false)
const batchResult = ref<QcCheckBatchResponse | null>(null)

/** 当前页可勾选的待检行 id（已出分行不重跑，不参与批量） */
const pendingIds = computed<string[]>(() =>
  pendingItems.value.map((item) => item.session_id),
)

/** 表头全选态：有待检行且全部已勾 */
const allChecked = computed<boolean>(
  () => pendingIds.value.length > 0
    && pendingIds.value.every((id) => selectedIds.value.includes(id)),
)

/** 表头半选态：有勾选但未全选（原生 checkbox 的 indeterminate 是 DOM 属性，需手动同步） */
const partiallyChecked = computed<boolean>(
  () => selectedIds.value.length > 0 && !allChecked.value,
)
const headerCheckRef = ref<HTMLInputElement | null>(null)
watchEffect(() => {
  if (headerCheckRef.value) {
    headerCheckRef.value.indeterminate = partiallyChecked.value
  }
})

// ── 灌数 ──
const seeding = ref(false)

// ── 原始回放（仅 DEV）──
const rawDialogOpen = ref(false)
const rawJsonText = ref('')
const rawSubmitting = ref(false)
const rawFormError = ref<string | null>(null)

/** 合并视图：待检行在前，已出分行在后；按 session_id 去重（待检优先） */
const rows = computed<QcBoardRow[]>(() => {
  const pendingRows: QcBoardRow[] = pendingItems.value.map((item) => ({
    session_id: item.session_id,
    scenario: item.scenario,
    label: item.label,
    case_id: item.case_id,
    call_id: item.call_id,
    kind: 'pending',
    qc_status: null,
    deduction_total: null,
    risk: null,
  }))
  const pendingIdSet = new Set(pendingItems.value.map((item) => item.session_id))
  const scoredRows: QcBoardRow[] = scoredItems.value
    .filter((item) => !pendingIdSet.has(item.session_id))
    .map((item) => ({
      session_id: item.session_id,
      scenario: null,
      label: null,
      case_id: item.case_id,
      call_id: null,
      kind: 'scored',
      qc_status: item.qc_status,
      deduction_total: item.deduction_total,
      risk: item.risk,
    }))
  return [...pendingRows, ...scoredRows]
})

/** 拉取双数据源（待检队列 + 已出分记录首页） */
async function fetchBoard(): Promise<void> {
  listLoading.value = true
  loadError.value = null
  try {
    const [pending, records] = await Promise.all([
      listQcDemoPending(),
      listQcRecords({ page: 1, page_size: 20 }),
    ])
    pendingItems.value = pending.items
    scoredItems.value = records.items
    // 队列刷新后清掉已不在待检集合里的勾选，避免批量提交陈旧 session
    const pendingIdSet = new Set(pending.items.map((item) => item.session_id))
    selectedIds.value = selectedIds.value.filter((id) => pendingIdSet.has(id))
  } catch (e) {
    loadError.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    listLoading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重——
// 进入页面只拉一次；缓存激活时再刷新（spec 列表页 keepAlive Scenario）
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchBoard()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchBoard()
})
onUnmounted(() => { unmountAbort.abort() })

/** 行内开检：POST replay/{session_id}；全程加载态 + 防连点 + 禁同行重复触发 */
async function startQc(row: QcBoardRow): Promise<void> {
  if (row.kind !== 'pending') return
  const id = row.session_id
  // A2003 已置灰的行不重复发起请求（spec「A2003 按钮置灰」）
  if (blockedReasons.value[id]) return
  // 防连点：同行在途直接忽略后续点击
  if (replayingIds.value.includes(id)) return
  replayingIds.value = [...replayingIds.value, id]
  try {
    await replayQcSession(id, {}, undefined, unmountAbort.signal)
    ElMessage.success(`开检完成：${id}`)
    await fetchBoard()
  } catch (e) {
    const err = e as RequestError
    if (err.code === 'A2003') {
      // 白名单动作：按钮置灰 + 悬浮后端原因（拦截器已附白名单文案 + trace 后 8 位）
      blockedReasons.value = { ...blockedReasons.value, [id]: err.message }
    } else {
      ElMessage.error(`开检失败：${err.message}`)
    }
  } finally {
    replayingIds.value = replayingIds.value.filter((x) => x !== id)
  }
}

/** 已出分行：仅跳详情（详情页拉 records/{session_id}），不自动重跑 */
function openRecord(row: QcBoardRow): void {
  void router.push(`/qc/records/${row.session_id}`)
}

/** 勾选切换：超过 QC_BATCH_LIMIT 拒绝勾选并提示（design D2） */
function onToggleSelect(row: QcBoardRow, event: Event): void {
  const target = event.target as HTMLInputElement
  const id = row.session_id
  if (target.checked) {
    if (selectedIds.value.length >= QC_BATCH_LIMIT) {
      // 超限：回退勾选并提示大批量走后端异步（P1）
      target.checked = false
      ElMessage.warning(`单次批量最多 ${QC_BATCH_LIMIT} 条，大批量走后端异步（P1）`)
      return
    }
    selectedIds.value = [...selectedIds.value, id]
  } else {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  }
}

/**
 * 表头全选/取消全选：
 * - 勾选时取当前页待检行前 QC_BATCH_LIMIT 条；待检数超上限时截断并提示（design D2 体验约束）；
 * - 取消勾选时清空全部已选。
 */
function onToggleSelectAll(event: Event): void {
  const target = event.target as HTMLInputElement
  if (!target.checked) {
    selectedIds.value = []
    return
  }
  const ids = pendingIds.value
  if (ids.length > QC_BATCH_LIMIT) {
    selectedIds.value = ids.slice(0, QC_BATCH_LIMIT)
    ElMessage.warning(
      `待检共 ${ids.length} 条，已选前 ${QC_BATCH_LIMIT} 条（单次批量上限），大批量走后端异步（P1）`,
    )
    return
  }
  selectedIds.value = [...ids]
}

/** 批量质检：POST check/batch（TIMEOUT.qcReplay 由 api 层保证）；结果分区展示 accepted/rejected */
async function submitBatch(): Promise<void> {
  if (batchSubmitting.value) return
  const ids = [...selectedIds.value]
  if (ids.length === 0) {
    ElMessage.warning('请先勾选待检会话')
    return
  }
  if (ids.length > QC_BATCH_LIMIT) {
    ElMessage.warning(`单次批量最多 ${QC_BATCH_LIMIT} 条，大批量走后端异步（P1）`)
    return
  }
  batchSubmitting.value = true
  try {
    const res = await checkQcBatch({ batch_id: randomUuid(), session_ids: ids }, unmountAbort.signal)
    batchResult.value = res
    selectedIds.value = []
    ElMessage.success(`批量质检完成：受理 ${res.accepted.length} 条，拒收 ${res.rejected.length} 条`)
    await fetchBoard()
  } catch (e) {
    ElMessage.error(`批量质检失败：${(e as Error).message}`)
  } finally {
    batchSubmitting.value = false
  }
}

/** 灌入演示数据（仅 DEV）：成功后刷新队列 */
async function onSeedDemo(): Promise<void> {
  if (seeding.value) return
  seeding.value = true
  try {
    const res = await seedQcDemo()
    ElMessage.success(`已灌入 ${res.session_ids.length} 条演示会话`)
    await fetchBoard()
  } catch (e) {
    ElMessage.error(`灌数失败：${(e as Error).message}`)
  } finally {
    seeding.value = false
  }
}

/** 打开原始回放对话框（仅 DEV） */
function openRawDialog(): void {
  rawFormError.value = null
  rawDialogOpen.value = true
}

/** 提交原始回放：A2002 表单行内标红（不叠 B0001），其余错误走提示 */
async function submitRawReplay(): Promise<void> {
  if (rawSubmitting.value) return
  rawFormError.value = null
  let body: QcReplayRawRequest
  try {
    body = JSON.parse(rawJsonText.value) as QcReplayRawRequest
  } catch {
    rawFormError.value = 'JSON 解析失败，请检查格式'
    return
  }
  rawSubmitting.value = true
  try {
    await replayQcRaw(body, undefined, unmountAbort.signal)
    rawDialogOpen.value = false
    rawJsonText.value = ''
    ElMessage.success('原始回放已受理')
    await fetchBoard()
  } catch (e) {
    const err = e as RequestError
    if (err.code === 'A2002') {
      // 白名单动作：表单行内标红（qc 的 422 不叠 B0001）
      rawFormError.value = err.message
    } else {
      ElMessage.error(`原始回放失败：${err.message}`)
    }
  } finally {
    rawSubmitting.value = false
  }
}
</script>

<template>
  <div class="qc-task-board">
    <header class="qtb-header">
      <h2 class="qtb-title">
        质检任务台
      </h2>
      <div class="qtb-tools">
        <button
          type="button"
          class="qtb-btn"
          :disabled="listLoading"
          @click="fetchBoard"
        >
          {{ listLoading ? '刷新中…' : '刷新队列' }}
        </button>
        <span class="qtb-selected">已选 {{ selectedIds.length }}/{{ QC_BATCH_LIMIT }}</span>
        <button
          type="button"
          class="qtb-btn qtb-btn--primary"
          :disabled="batchSubmitting || selectedIds.length === 0"
          @click="submitBatch"
        >
          {{ batchSubmitting ? '批量质检中…' : '批量质检' }}
        </button>
        <!-- DEV 门控：演示入口仅开发环境渲染（生产构建不出现入口与触发器） -->
        <template v-if="isDev">
          <button
            type="button"
            class="qtb-btn qtb-btn--ghost"
            :disabled="seeding"
            @click="onSeedDemo"
          >
            {{ seeding ? '灌数中…' : '灌入演示数据' }}
          </button>
          <button
            type="button"
            class="qtb-btn qtb-btn--ghost"
            @click="openRawDialog"
          >
            原始回放
          </button>
        </template>
      </div>
    </header>

    <!-- 列表三态：loading > error（重试）> normal/empty -->
    <div
      v-if="listLoading && !rows.length"
      class="qtb-skeleton"
    >
      加载中…
    </div>
    <div
      v-else-if="loadError"
      class="qtb-error"
      role="alert"
    >
      <span>队列加载失败：{{ loadError.message }}</span>
      <button
        type="button"
        class="qtb-btn"
        @click="fetchBoard"
      >
        重试
      </button>
    </div>
    <div
      v-else-if="!rows.length"
      class="qtb-empty"
    >
      暂无待检会话
    </div>

    <table
      v-else
      class="qtb-table"
    >
      <thead>
        <tr>
          <th
            class="qtb-th qtb-th--check"
            aria-label="勾选"
          >
            <!-- 表头全选：仅作用于待检行；部分勾选时呈半选态（indeterminate 由 watchEffect 同步） -->
            <input
              ref="headerCheckRef"
              type="checkbox"
              class="qtb-check"
              :checked="allChecked"
              :disabled="batchSubmitting || pendingIds.length === 0"
              aria-label="全选待检会话"
              @change="onToggleSelectAll"
            >
          </th>
          <th class="qtb-th">
            场景
          </th>
          <th class="qtb-th">
            会话 ID
          </th>
          <th class="qtb-th">
            案件 ID
          </th>
          <th class="qtb-th">
            通话 ID
          </th>
          <th class="qtb-th">
            状态
          </th>
          <th class="qtb-th">
            扣分
          </th>
          <th class="qtb-th">
            操作
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.session_id"
          class="qtb-tr"
          :data-session-id="row.session_id"
          :data-kind="row.kind"
        >
          <td class="qtb-td qtb-td--check">
            <!-- 仅待检行可勾选批量；已出分行不重跑故不可勾选 -->
            <input
              v-if="row.kind === 'pending'"
              type="checkbox"
              class="qtb-check"
              :checked="selectedIds.includes(row.session_id)"
              :disabled="batchSubmitting"
              :aria-label="`勾选 ${row.session_id}`"
              @change="onToggleSelect(row, $event)"
            >
          </td>
          <td class="qtb-td">
            {{ row.label ?? row.scenario ?? '—' }}
          </td>
          <td class="qtb-td">
            <code>{{ row.session_id }}</code>
          </td>
          <td class="qtb-td">
            <code>{{ row.case_id ?? '—' }}</code>
          </td>
          <td class="qtb-td">
            <code>{{ row.call_id ?? '—' }}</code>
          </td>
          <td class="qtb-td">
            <span
              v-if="row.kind === 'pending'"
              class="qtb-tag qtb-tag--pending"
            >待检</span>
            <span
              v-else
              class="qtb-tag qtb-tag--scored"
            >
              {{ row.qc_status ? QC_STATUS_LABELS[row.qc_status] : '已出分' }}
            </span>
          </td>
          <td class="qtb-td">
            {{ row.deduction_total === null ? '—' : `-${row.deduction_total}` }}
          </td>
          <td class="qtb-td">
            <!-- 待检行：开始质检；A2003 置灰并悬浮后端原因，不再发起请求 -->
            <button
              v-if="row.kind === 'pending'"
              type="button"
              class="qtb-action"
              :disabled="replayingIds.includes(row.session_id) || !!blockedReasons[row.session_id]"
              :title="blockedReasons[row.session_id] || undefined"
              @click="startQc(row)"
            >
              {{ replayingIds.includes(row.session_id) ? '开检中…' : '开始质检' }}
            </button>
            <!-- 已出分行：仅跳详情，不自动重跑 -->
            <button
              v-else
              type="button"
              class="qtb-action qtb-action--ghost"
              @click="openRecord(row)"
            >
              查看详情
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 批量结果分区：accepted / rejected（rejected 附后端 code） -->
    <section
      v-if="batchResult"
      class="qtb-batch-result"
      aria-label="批量质检结果"
    >
      <header class="qtb-batch-head">
        <h3>批量结果（batch_id: <code>{{ batchResult.batch_id }}</code>）</h3>
        <button
          type="button"
          class="qtb-btn"
          @click="batchResult = null"
        >
          关闭
        </button>
      </header>
      <div class="qtb-batch-cols">
        <div class="qtb-batch-col">
          <h4>已受理（{{ batchResult.accepted.length }}）</h4>
          <ul v-if="batchResult.accepted.length">
            <li
              v-for="item in batchResult.accepted"
              :key="item.session_id ?? ''"
            >
              <code>{{ item.session_id }}</code>
              · {{ item.qc_status ? QC_STATUS_LABELS[item.qc_status] : '—' }}
              · 扣分 {{ item.deduction_total === null ? '—' : `-${item.deduction_total}` }}
            </li>
          </ul>
          <p
            v-else
            class="qtb-muted"
          >
            无
          </p>
        </div>
        <div class="qtb-batch-col">
          <h4>已拒收（{{ batchResult.rejected.length }}）</h4>
          <ul v-if="batchResult.rejected.length">
            <li
              v-for="item in batchResult.rejected"
              :key="item.session_id"
            >
              <code>{{ item.session_id }}</code> · <span class="qtb-rejected-code">code={{ item.code }}</span>
            </li>
          </ul>
          <p
            v-else
            class="qtb-muted"
          >
            无
          </p>
        </div>
      </div>
    </section>

    <!-- 原始回放对话框（仅 DEV；A2002 行内标红） -->
    <div
      v-if="rawDialogOpen"
      class="qtb-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      @click.self="rawDialogOpen = false"
    >
      <div class="qtb-dialog">
        <h3>原始会话回放（replay/raw）</h3>
        <p class="qtb-muted">
          粘贴原始会话 JSON（RawQcSession 结构），每次提交自动携带新 X-Idempotency-Key。
        </p>
        <textarea
          v-model="rawJsonText"
          class="qtb-textarea"
          :class="{ 'qtb-field--error': !!rawFormError }"
          rows="8"
          placeholder="{&quot;session_id&quot;:&quot;...&quot;,&quot;case_id&quot;:&quot;...&quot;,&quot;call_ended&quot;:true,&quot;turns&quot;:[...]}"
        />
        <p
          v-if="rawFormError"
          class="qtb-field-error"
          role="alert"
        >
          {{ rawFormError }}
        </p>
        <div class="qtb-dialog-footer">
          <button
            type="button"
            class="qtb-btn"
            @click="rawDialogOpen = false"
          >
            取消
          </button>
          <button
            type="button"
            class="qtb-btn qtb-btn--primary"
            :disabled="rawSubmitting || !rawJsonText.trim()"
            @click="submitRawReplay"
          >
            {{ rawSubmitting ? '提交中…' : '提交回放' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qc-task-board { padding: 16px 20px; }
.qtb-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.qtb-title { margin: 0; font-size: 18px; color: #303133; }
.qtb-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.qtb-selected { font-size: 12px; color: #909399; }
.qtb-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qtb-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.qtb-btn--ghost { color: #909399; border-color: #ebeef5; }
.qtb-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.qtb-skeleton { padding: 48px 0; text-align: center; color: #909399; font-size: 14px; }
.qtb-error {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  padding: 48px 0; color: #f56c6c; font-size: 14px;
}
.qtb-empty { padding: 48px 0; text-align: center; color: #909399; font-size: 14px; }

.qtb-table { width: 100%; border-collapse: collapse; border: 1px solid #ebeef5; background: white; }
.qtb-th, .qtb-td { padding: 10px 14px; border-bottom: 1px solid #ebeef5; text-align: left; }
.qtb-th { background: #fafafa; font-weight: 600; color: #606266; font-size: 13px; }
.qtb-th--check, .qtb-td--check { width: 36px; text-align: center; }
.qtb-tr:hover { background: #f5f7fa; }
.qtb-td { font-size: 13px; color: #303133; }
.qtb-td code { font-size: 12px; color: #606266; }

.qtb-tag {
  display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px;
}
.qtb-tag--pending { background: #fdf6ec; color: #e6a23c; border: 1px solid #f5dab1; }
.qtb-tag--scored { background: #f0f9eb; color: #67c23a; border: 1px solid #c2e7b0; }

.qtb-action {
  padding: 4px 12px; border: 1px solid #409eff; background: #409eff; color: white;
  border-radius: 4px; cursor: pointer; font-size: 12px;
}
.qtb-action--ghost { background: white; color: #409eff; }
.qtb-action:disabled { opacity: 0.5; cursor: not-allowed; border-color: #dcdfe6; background: #f5f7fa; color: #909399; }

.qtb-batch-result {
  margin-top: 16px; border: 1px solid #ebeef5; border-radius: 6px; padding: 12px 16px;
  background: #fafbfc;
}
.qtb-batch-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.qtb-batch-head h3 { margin: 0; font-size: 14px; }
.qtb-batch-cols { display: flex; gap: 24px; flex-wrap: wrap; }
.qtb-batch-col { flex: 1; min-width: 240px; }
.qtb-batch-col h4 { margin: 0 0 6px; font-size: 13px; color: #606266; }
.qtb-batch-col ul { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.8; }
.qtb-rejected-code { color: #f56c6c; }
.qtb-muted { margin: 0; font-size: 12px; color: #909399; }

.qtb-dialog-backdrop {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.4);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.qtb-dialog {
  background: white; border-radius: 8px; padding: 24px; width: 560px;
  display: flex; flex-direction: column; gap: 12px;
}
.qtb-dialog h3 { margin: 0; font-size: 16px; }
.qtb-textarea {
  width: 100%; padding: 8px; border: 1px solid #dcdfe6; border-radius: 4px;
  resize: vertical; box-sizing: border-box; font-family: monospace; font-size: 12px;
}
.qtb-field--error { border-color: #f56c6c; }
.qtb-field-error { margin: 0; font-size: 12px; color: #f56c6c; }
.qtb-dialog-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
