<script setup lang="ts">
/**
 * KbDocuments：文档管理（f2-rag-console 批次③ / tasks 4.1-4.4）。
 *
 * 规格（spec「文档详情与维护操作必须如实呈现后端语义」+ 错误处理白名单）：
 * - 列表：PageTable + 分类/状态筛选（含「全部」）+ 分页；列对齐 formatters.py:36
 *   DOC_TABLE_HEADERS（标题/分类/status/stage/chunks/创建时间/doc_id），分类映射对齐
 *   formatters.py:22-27；查询条件与分页为组件本地 ref，不进 store（design D5）；
 *   status 列用 StatusTag（PageTable render 仅支持原始值，故 status 起各列经
 *   #table-row-extra 多单元格 slot 渲染，同 QcRecordList 操作列先例）；
 * - keepAlive：onMounted 拉一次 + onActivated 刷新，首个 activated 与 mounted 同序
 *   触发，标志位去重（照 QcRecordList 先例）；
 * - 行点击进入详情抽屉（KbDocDetailDrawer，props 进 emit 出；详情数据由本容器获取
 *   后传入）；B0004 → 抽屉空态 + 返回列表，并后台刷新列表剔除已不存在行；
 * - 摘要编辑：ElDialog + el-form rules（≤200 字，KB_SUMMARY_MAX_LEN），超长行内标红
 *   不发请求；B0001 行内透出（不弹全局 toast），其余码 ElMessage 直出；
 * - 删除/重建：ElMessageBox 二次确认；重建确认框注明「仅按正文重建，不补全表格/图片
 *   资产」；reindex 为同步长任务——超时类错误（拦截器网络层错误无 code，design D7）
 *   提示「操作已提交，请刷新列表查看 status 终态」，不自动重试；成功后刷新列表；
 * - 错误处理严格按 §15.1 白名单：不新增错误码映射，未列入码走默认分支 message 直出
 *   （拦截器已附兜底文案 + trace 后 8 位）；
 * - 上传（tasks 4.5/4.6）：KbUploadDialog 纯展示组件（白名单本地校验 + 四档预览），
 *   本容器负责请求与编排——异步入库早返回后刷新列表露出 processing 行，随后经
 *   useKbIngestPolling 轮询该文档详情直至 active/failed（终态自停/卸载清理/失败熔断/
 *   页面隐藏暂停）；轮询快照同步进列表行，stage 列对 processing 行按后端
 *   ingest_progress（stage→progress 映射，README §G）展示进度条；failed 终态
 *   ElMessage 透出 ingest_error；B0001 在对话框文件行内透出，A3001 等走默认分支
 *   message 直出；
 * - 按钮级权限（tasks 7.1，spec「按钮级权限必须按后端 allowlist 对 ai_trainer 置灰」）：
 *   上传/删除/重建索引/摘要编辑按后端词表 admin/supervisor（§5.0 F）判定
 *   （useKbWriteAllowed，角色经 inject 桥读取，不 import app store）；越权角色
 *   （含 ai_trainer）置灰 + el-tooltip 悬浮说明「该操作需 admin/supervisor 权限」，
 *   且各操作入口函数前置守卫，置灰态不发起请求。
 */
import { onActivated, onDeactivated, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance } from 'element-plus'
import PageTable from '@/shared/components/base/PageTable.vue'
import type { Column } from '@/shared/components/base/PageTable.vue'
import StatusTag from '@/shared/components/base/StatusTag.vue'
import type { RequestError } from '@/shared/request'
import {
  deleteKbDocument,
  getKbDocument,
  listKbDocuments,
  reindexKbDocument,
  updateKbDocumentSummary,
  uploadKbDocument,
} from '../api'
import type { RagCategory, RagDocStatus, RagDocumentDetailResponse, RagDocumentListItem } from '../types'
import { KB_SUMMARY_MAX_LEN, RAG_CATEGORY_LABELS } from '../types'
import KbDocDetailDrawer from '../components/KbDocDetailDrawer.vue'
import KbUploadDialog from '../components/KbUploadDialog.vue'
import { useKbIngestPolling } from '../composables/useKbIngestPolling'
import { KB_SUMMARY_RULES } from '../composables/kbSummaryRules'
import { RAG_WRITE_FORBIDDEN_TIP, useKbWriteAllowed } from '../composables/useCurrentRole'

defineOptions({ name: 'KbDocuments' })

// ── 按钮级权限（tasks 7.1）：写操作按后端词表 admin/supervisor（§5.0 F）──
const writeAllowed = useKbWriteAllowed()

// ── 筛选项（「全部」= 空值，不下发该参数；状态值与后端枚举逐字一致，前端不发明状态）──
const CATEGORY_OPTIONS: { value: RagCategory | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'regulation', label: RAG_CATEGORY_LABELS.regulation },
  { value: 'script', label: RAG_CATEGORY_LABELS.script },
  { value: 'case', label: RAG_CATEGORY_LABELS.case },
  { value: 'other', label: RAG_CATEGORY_LABELS.other },
]
// 状态选项对齐 formatters.py:31 STATUS_CHOICES（原始英文值直出）
const STATUS_OPTIONS: { value: RagDocStatus | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'active', label: 'active' },
  { value: 'processing', label: 'processing' },
  { value: 'failed', label: 'failed' },
  { value: 'archived', label: 'archived' },
]

// ── 列表状态（组件本地 ref，不进 store，design D5）──
const rows = ref<RagDocumentListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref<Error | null>(null)
const categoryFilter = ref<RagCategory | ''>('')
const statusFilter = ref<RagDocStatus | ''>('')

async function fetchList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listKbDocuments({
      page: page.value,
      page_size: pageSize.value,
      ...(categoryFilter.value ? { category: categoryFilter.value } : {}),
      ...(statusFilter.value ? { status: statusFilter.value } : {}),
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    // B0006 等：拦截器已附白名单文案 + trace 后 8 位，PageTable 错误态直出，不自动重试
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重（同 QcRecordList 先例）
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchList()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchList()
  // deactivated 时停止的轮询：仍有未终态文档则恢复（P2-4 多文档队列续航）
  if (pendingDocIds.value.length > 0 && ingestPolling.isStopped.value) {
    void ingestPolling.start()
  }
})

function onPageChange(p: number): void {
  page.value = p
  void fetchList()
}
function onPageSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  void fetchList()
}
/** 筛选变更：回到第一页重新查询 */
function onFilterChange(): void {
  page.value = 1
  void fetchList()
}
function onRetry(): void {
  void fetchList()
}

// ── 列定义：标题/分类走 columns；status 起各列经 #table-row-extra 渲染（StatusTag 组件）──
const columns: Column<RagDocumentListItem>[] = [
  { key: 'title', label: '标题', render: (row) => row.title },
  { key: 'category', label: '分类', width: 100, render: (row) => RAG_CATEGORY_LABELS[row.category] },
]

// ── 详情抽屉（数据由本容器获取后传入，抽屉不碰 api）──
const drawerOpen = ref(false)
const detail = ref<RagDocumentDetailResponse | null>(null)
const detailLoading = ref(false)
const detailNotFound = ref(false)
const detailError = ref<string | null>(null)
const detailErrorTrace = ref<string | null>(null)
/** 当前打开的 doc_id：错误态重试时详情可能为 null，需独立持有 */
const currentDocId = ref<string | null>(null)

async function fetchDetail(docId: string): Promise<void> {
  detailLoading.value = true
  detail.value = null
  detailNotFound.value = false
  detailError.value = null
  detailErrorTrace.value = null
  try {
    detail.value = await getKbDocument(docId)
  } catch (e) {
    const err = e as RequestError
    // §15.1 白名单：B0004 → 详情空态 + 返回列表；后台刷新列表剔除已不存在行
    if (err.code === 'B0004') {
      detailNotFound.value = true
      void fetchList()
    } else {
      // B0007/B0008/B0009 兜底与其余码：message 直出 + TraceIdText（拦截器已附文案与 trace）
      detailError.value = err.message ?? '加载失败'
      detailErrorTrace.value = err.trace ?? null
    }
  } finally {
    detailLoading.value = false
  }
}

function openDetail(row: RagDocumentListItem): void {
  currentDocId.value = row.id
  drawerOpen.value = true
  void fetchDetail(row.id)
}

/** 抽屉错误态重试 */
function retryDetail(): void {
  if (currentDocId.value) void fetchDetail(currentDocId.value)
}

/** 行点击进入详情抽屉（事件委托：PageTable 行 class 固定为 pt-tr，按 tbody 内序号映射行数据） */
function onTableClick(e: MouseEvent): void {
  const tr = (e.target as HTMLElement).closest('tr.pt-tr')
  if (!tr || !tr.parentElement) return
  const idx = Array.from(tr.parentElement.children).indexOf(tr)
  const row = rows.value[idx]
  if (row) openDetail(row)
}

// ── 摘要编辑对话框（tasks 4.3；≤200 字 rules 校验，超长行内标红不发请求）──
const summaryDialogOpen = ref(false)
const summarySubmitting = ref(false)
const summaryFormRef = ref<FormInstance>()
const summaryForm = reactive({ summary: '' })
/** B0001 行内透出（不弹全局 toast，§15.1 白名单动作） */
const summaryServerError = ref('')

function openSummaryDialog(): void {
  // 按钮级权限守卫：置灰态不打开对话框、不发起请求（tasks 7.1）
  if (!writeAllowed.value) return
  if (!detail.value) return
  summaryForm.summary = detail.value.summary ?? ''
  summaryServerError.value = ''
  summaryDialogOpen.value = true
}

async function submitSummary(): Promise<void> {
  const doc = detail.value
  if (!doc || summarySubmitting.value) return
  summaryServerError.value = ''
  // 前端 rules 校验：超长行内标红，请求不发出（spec「摘要超长前端拦截」）
  try {
    await summaryFormRef.value?.validate()
  } catch {
    return
  }
  summarySubmitting.value = true
  try {
    const res = await updateKbDocumentSummary(doc.id, { summary: summaryForm.summary })
    ElMessage.success('摘要已更新')
    summaryDialogOpen.value = false
    // 详情同步刷新（摘要列不在列表列内，无需刷新列表）
    detail.value = { ...doc, summary: res.summary }
  } catch (e) {
    const err = e as RequestError
    // B0001 → 表单行内透出，不弹全局 toast；其余码默认分支 message 直出
    if (err.code === 'B0001') {
      summaryServerError.value = err.message ?? '参数校验失败'
    } else {
      ElMessage.error(err.message ?? '摘要更新失败')
    }
  } finally {
    summarySubmitting.value = false
  }
}

// ── 上传对话框（tasks 4.5；展示组件只 emit 意图，请求与错误处置在本容器）──
const uploadDialogOpen = ref(false)
const uploadSubmitting = ref(false)
/** B0001（重名/参数）行内透出，不弹全局 toast（§15.1 白名单动作） */
const uploadServerError = ref('')

function openUploadDialog(): void {
  // 按钮级权限守卫：置灰态不打开对话框、不发起请求（tasks 7.1）
  if (!writeAllowed.value) return
  uploadServerError.value = ''
  uploadDialogOpen.value = true
}

// ── 入库轮询（tasks 4.6 / design D2）：轮询详情直至 active/failed，快照同步列表行 ──
/** 待终态文档集合（P2-4）：新上传只追加 id，不打断进行中的轮询 */
const pendingDocIds = ref<string[]>([])
const ingestPolling = useKbIngestPolling<RagDocumentDetailResponse[]>({
  poll: async () => {
    // 并发轮询全部待终态文档详情（README §G：列表或详情皆可）；快照同步进列表行，stage 列实时推进
    const docs = await Promise.all(pendingDocIds.value.map((id) => getKbDocument(id)))
    for (const doc of docs) {
      const row = rows.value.find((item) => item.id === doc.id)
      if (row) {
        row.status = doc.status
        row.ingest_stage = doc.ingest_stage
        row.ingest_progress = doc.ingest_progress
        row.ingest_error = doc.ingest_error
        row.chunks_count = doc.chunks_count
      }
      // 终态逐篇提示并移出待轮询集合，避免无限重拉（多文档并行时不会漏报）
      if (doc.status === 'active') {
        ElMessage.success(`文档「${doc.title}」入库完成`)
      } else if (doc.status === 'failed') {
        // failed 终态透出 ingest_error（spec「上传必须为异步入库并轮询至终态」）
        ElMessage.error(`文档「${doc.title}」入库失败：${doc.ingest_error ?? '原因未知'}`)
      }
    }
    pendingDocIds.value = pendingDocIds.value.filter((id) => {
      const doc = docs.find((item) => item.id === id)
      return !!doc && doc.status !== 'active' && doc.status !== 'failed'
    })
    return docs
  },
  // 待轮询集合清空即全部到达终态，自停
  isTerminal: () => pendingDocIds.value.length === 0,
  onError: (message) => {
    ElMessage.error(message)
  },
})

// keepAlive 视图：离开页面（deactivated）即停轮询，无后台请求继续发出
// （spec「组件卸载后无残留定时器」；composables 侧另有 onScopeDispose 卸载清理）
onDeactivated(() => {
  ingestPolling.stop()
})

async function onUploadSubmit(payload: {
  file: File
  category: RagCategory
  summary: string
}): Promise<void> {
  if (uploadSubmitting.value) return
  uploadServerError.value = ''
  uploadSubmitting.value = true
  try {
    const res = await uploadKbDocument(payload.file, payload.category, payload.summary || undefined)
    // 异步入库早返回 {doc_id, status:"processing"}（README §G BREAKING）
    ElMessage.success('上传成功，正在入库')
    uploadDialogOpen.value = false
    // 先刷新列表露出 processing 行，再追加轮询目标（P2-4：不打断其他文档的在途轮询）
    await fetchList()
    if (!pendingDocIds.value.includes(res.doc_id)) {
      pendingDocIds.value = [...pendingDocIds.value, res.doc_id]
    }
    if (ingestPolling.isPolling.value) {
      // 已在轮询中：立即补一次让新文档状态同步，不等下一个 3s 周期
      void ingestPolling.pollNow()
    } else {
      void ingestPolling.start()
    }
  } catch (e) {
    const err = e as RequestError
    // B0001（重名/参数）→ 对话框文件行内透出，不弹全局 toast；
    // A3001（不支持格式）等其余码 → 默认分支 message 直出（§15.1 白名单零新增）
    if (err.code === 'B0001') {
      uploadServerError.value = err.message ?? '参数校验失败'
    } else {
      ElMessage.error(err.message ?? '上传失败')
    }
  } finally {
    uploadSubmitting.value = false
  }
}

// ── 删除 / 重建索引（tasks 4.4；ElMessageBox 二次确认，成功后刷新列表）──
const actionPending = ref(false)

async function onDelete(): Promise<void> {
  const doc = detail.value
  // 按钮级权限守卫：置灰态不发起请求（tasks 7.1）
  if (!writeAllowed.value) return
  if (!doc || actionPending.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除文档「${doc.title}」？删除后正文、索引与资产将一并移除，不可恢复。`,
      '删除文档确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消：不发起请求
    return
  }
  actionPending.value = true
  try {
    await deleteKbDocument(doc.id)
    ElMessage.success('文档已删除')
    drawerOpen.value = false
    await fetchList()
  } catch (e) {
    ElMessage.error((e as RequestError).message ?? '删除失败')
  } finally {
    actionPending.value = false
  }
}

async function onReindex(): Promise<void> {
  const doc = detail.value
  // 按钮级权限守卫：置灰态不发起请求（tasks 7.1）
  if (!writeAllowed.value) return
  if (!doc || actionPending.value) return
  try {
    // 如实呈现后端语义（README §G reindex 行 / documents.py:236）：仅按 PG 正文重切，
    // 不补全表格/图片资产；MUST NOT 暗示重建补全资产
    await ElMessageBox.confirm(
      `确认重建文档「${doc.title}」的索引？重建仅按正文重新切分与索引，不补全表格/图片资产；如需表格增强请删除后重新上传。`,
      '重建索引确认',
      { type: 'warning', confirmButtonText: '确认重建', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消：不发起请求
    return
  }
  actionPending.value = true
  try {
    await reindexKbDocument(doc.id)
    ElMessage.success('重建索引完成')
    await fetchList()
    await fetchDetail(doc.id)
  } catch (e) {
    const err = e as RequestError
    // design D7：reindex 为同步长任务，12s 超时/网络层错误（无 code）时后端已先置
    // processing（documents.py:260）——提示引导刷新查 status 终态，不自动重试
    if (!err.code) {
      ElMessage.warning('操作已提交，请刷新列表查看 status 终态')
      await fetchList()
    } else {
      ElMessage.error(err.message ?? '重建索引失败')
    }
  } finally {
    actionPending.value = false
  }
}
</script>

<template>
  <div class="kb-documents">
    <header class="kbd-header">
      <h2 class="kbd-title">
        文档管理
      </h2>
      <div class="kbd-tools">
        <!-- 分类筛选（含「全部」= 空值不下发） -->
        <select
          v-model="categoryFilter"
          class="kbd-filter"
          aria-label="分类筛选"
          @change="onFilterChange"
        >
          <option
            v-for="opt in CATEGORY_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
        <!-- 状态筛选（含「全部」；枚举值与后端逐字一致） -->
        <select
          v-model="statusFilter"
          class="kbd-filter"
          aria-label="状态筛选"
          @change="onFilterChange"
        >
          <option
            v-for="opt in STATUS_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </option>
        </select>
        <!-- 上传：按钮级权限（tasks 7.1）——越权角色置灰 + 悬浮说明，置灰不发起请求；
             disabled 按钮不触发 hover，故外层包 span 供 el-tooltip 监听 -->
        <el-tooltip
          :disabled="writeAllowed"
          :content="RAG_WRITE_FORBIDDEN_TIP"
          placement="top"
        >
          <span class="kbd-tooltip-wrap">
            <button
              type="button"
              class="kbd-btn kbd-btn--primary"
              :disabled="!writeAllowed"
              @click="openUploadDialog"
            >
              上传文档
            </button>
          </span>
        </el-tooltip>
        <button
          type="button"
          class="kbd-btn"
          :disabled="loading"
          @click="onRetry"
        >
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- 列表（行点击进详情抽屉；status 起各列经 extra slot 渲染以使用 StatusTag） -->
    <div @click="onTableClick">
      <PageTable
        :columns="columns as Column<unknown>[]"
        :rows="rows as unknown[]"
        :total="total"
        :loading="loading"
        :error="error"
        :page="page"
        :page-size="pageSize"
        row-key="id"
        empty-text="暂无文档"
        @update:page="onPageChange"
        @update:page-size="onPageSizeChange"
        @retry="onRetry"
      >
        <template #table-head-extra>
          <th
            class="pt-th"
            style="width: 110px"
          >
            status
          </th>
          <th
            class="pt-th"
            style="width: 170px"
          >
            stage
          </th>
          <th
            class="pt-th"
            style="width: 70px"
          >
            chunks
          </th>
          <th
            class="pt-th"
            style="width: 180px"
          >
            创建时间
          </th>
          <th
            class="pt-th"
            style="width: 240px"
          >
            doc_id
          </th>
          <th
            class="pt-th"
            style="width: 90px"
          >
            操作
          </th>
        </template>
        <template #table-row-extra="{ row }">
          <td class="pt-td">
            <StatusTag :status="(row as RagDocumentListItem).status" />
          </td>
          <td class="pt-td">
            <!-- processing 行按后端 ingest_progress（stage→progress 映射，README §G）
                 展示进度条；其余态直出 stage 原文 -->
            <div
              v-if="(row as RagDocumentListItem).status === 'processing'"
              class="kbd-stage"
            >
              <span>{{ (row as RagDocumentListItem).ingest_stage ?? '—' }}</span>
              <el-progress
                :percentage="(row as RagDocumentListItem).ingest_progress ?? 0"
                :stroke-width="6"
                class="kbd-stage-progress"
              />
            </div>
            <span v-else>{{ (row as RagDocumentListItem).ingest_stage ?? '—' }}</span>
          </td>
          <td class="pt-td">
            {{ (row as RagDocumentListItem).chunks_count }}
          </td>
          <td class="pt-td">
            {{ (row as RagDocumentListItem).create_time || '—' }}
          </td>
          <td class="pt-td">
            <code class="kbd-doc-id">{{ (row as RagDocumentListItem).id }}</code>
          </td>
          <td class="pt-td">
            <button
              type="button"
              class="kbd-action"
              @click.stop="openDetail(row as RagDocumentListItem)"
            >
              详情
            </button>
          </td>
        </template>
      </PageTable>
    </div>

    <!-- 详情抽屉（props 进 emit 出；操作确认与请求由本容器负责；
         can-manage 驱动底栏写操作按钮置灰 + 悬浮说明，tasks 7.1） -->
    <KbDocDetailDrawer
      v-model="drawerOpen"
      :detail="detail"
      :loading="detailLoading"
      :not-found="detailNotFound"
      :error-message="detailError"
      :error-trace="detailErrorTrace"
      :action-pending="actionPending"
      :can-manage="writeAllowed"
      @retry="retryDetail"
      @edit-summary="openSummaryDialog"
      @reindex="onReindex"
      @delete="onDelete"
    />

    <!-- 上传对话框（tasks 4.5/4.6）：纯展示组件，请求/错误处置/轮询编排在本容器 -->
    <KbUploadDialog
      v-model="uploadDialogOpen"
      v-model:server-error="uploadServerError"
      :submitting="uploadSubmitting"
      @submit="onUploadSubmit"
    />

    <!-- 摘要编辑对话框：rules ≤200 字，超长行内标红不发请求；B0001 行内透出 -->
    <el-dialog
      v-model="summaryDialogOpen"
      title="编辑摘要"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="summaryFormRef"
        :model="summaryForm"
        :rules="KB_SUMMARY_RULES"
      >
        <el-form-item
          prop="summary"
          :error="summaryServerError || undefined"
        >
          <el-input
            v-model="summaryForm.summary"
            type="textarea"
            :rows="6"
            :placeholder="`请输入文档摘要（不超过 ${KB_SUMMARY_MAX_LEN} 字）`"
            @input="summaryServerError = ''"
          />
        </el-form-item>
      </el-form>
      <p class="kbd-summary-hint">
        {{ summaryForm.summary.length }}/{{ KB_SUMMARY_MAX_LEN }}
      </p>
      <template #footer>
        <el-button @click="summaryDialogOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="summarySubmitting"
          @click="submitSummary"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-documents { padding: 16px 20px; }
.kbd-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
}
.kbd-title { margin: 0; font-size: 18px; color: #303133; }
.kbd-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.kbd-filter {
  padding: 4px 8px; border: 1px solid #dcdfe6; border-radius: 4px; font-size: 13px;
}
.kbd-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.kbd-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.kbd-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.kbd-tooltip-wrap { display: inline-block; }
.kbd-stage { display: flex; align-items: center; gap: 8px; }
.kbd-stage-progress { flex: 1; min-width: 0; }
.kbd-stage-progress :deep(.el-progress__text) { min-width: auto; font-size: 12px !important; }
.kbd-doc-id { font-family: monospace; font-size: 12px; }
.kbd-action {
  padding: 4px 12px; border: 1px solid #409eff; background: white; color: #409eff;
  border-radius: 4px; cursor: pointer; font-size: 12px;
}
.kbd-summary-hint { margin: 4px 0 0; font-size: 12px; color: #909399; text-align: right; }
</style>
