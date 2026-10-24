<template>
  <div class="review-workbench">
    <!-- 左栏：待复核队列 -->
    <aside class="rw-queue">
      <header class="rw-queue-head">
        <h3>待复核队列</h3>
        <span class="rw-count">{{ items.length }} 条</span>
        <button type="button" class="rw-refresh" :disabled="listLoading" @click="fetchQueue">
          {{ listLoading ? '刷新中…' : '刷新' }}
        </button>
      </header>

      <el-alert
        v-if="listError"
        type="error"
        :closable="false"
        show-icon
        :title="`队列加载失败：${listError.message}`"
      />
      <el-skeleton v-else-if="listLoading && !items.length" :rows="6" animated />

      <ul v-else class="rw-list">
        <li
          v-for="item in items"
          :key="item.complaint_id"
          class="rw-item"
          :class="{ 'rw-item--active': item.complaint_id === selectedId }"
          @click="selectItem(item.complaint_id)"
        >
          <div class="rw-item-top">
            <code class="rw-id">{{ shortId(item.complaint_id) }}</code>
            <el-tag size="small" :type="statusTagType(item.task_status)">
              {{ STATUS_LABELS[item.task_status] }}
            </el-tag>
          </div>
          <p class="rw-text">{{ item.complaint_text }}</p>
          <div class="rw-item-bottom">
            <span class="rw-verdict">{{ item.verdict ? VERDICT_LABELS[item.verdict] : '待研判' }}</span>
            <span class="rw-time">{{ item.created_at }}</span>
          </div>
        </li>
      </ul>

      <el-empty v-if="!listLoading && !items.length && !listError" description="暂无待复核工单" :image-size="60" />
    </aside>

    <!-- 右栏：复核面板（原地处理，不跳详情页） -->
    <section class="rw-panel">
      <el-empty v-if="!selectedId" description="从左侧选择一条工单开始复核" :image-size="80" />

      <template v-else>
        <header class="rw-panel-head">
          <div>
            <h3>复核：{{ shortId(selectedId) }}</h3>
            <el-tag v-if="detail" size="small" :type="statusTagType(detail.task_status)">
              {{ STATUS_LABELS[detail.task_status] }}
            </el-tag>
          </div>
          <el-button size="small" @click="openFullDetail">打开完整详情</el-button>
        </header>

        <el-skeleton v-if="detailLoading" :rows="6" animated />
        <el-alert v-else-if="detailError" type="error" :closable="false" show-icon :title="`详情加载失败：${detailError.message}`" />

        <template v-else-if="detail">
          <!-- 投诉原文 -->
          <div class="rw-block">
            <h4>投诉原文</h4>
            <p class="rw-raw">{{ detail.complaint_text }}</p>
          </div>

          <!-- AI 判责 -->
          <div class="rw-block">
            <h4>AI 研判结论</h4>
            <div class="rw-kv">
              <span>AI 初选</span>
              <strong>{{ detail.ai_verdict ? VERDICT_LABELS[detail.ai_verdict] : '—' }}</strong>
              <span>置信度</span>
              <strong>{{ detail.confidence === null ? '—' : `${Math.round(detail.confidence * 100)}%` }}</strong>
              <span>人审定责</span>
              <strong>{{ detail.disposition_verdict ? VERDICT_LABELS[detail.disposition_verdict] : '未定责' }}</strong>
            </div>
          </div>

          <!-- AI 取证链 -->
          <div v-if="chainItems.length" class="rw-block">
            <h4>AI 取证链（{{ chainItems.length }} 条）</h4>
            <ol class="rw-evidence">
              <li v-for="(ev, i) in chainItems" :key="i">
                <el-tag size="small" class="rw-ev-tag">{{ ev.category }}</el-tag>
                <strong>{{ ev.name }}</strong>
                <span class="rw-ev-summary">{{ ev.summary }}</span>
              </li>
            </ol>
          </div>

          <!-- 应答草稿 -->
          <div class="rw-block">
            <h4>
              应答草稿
              <el-button v-if="detail.response_draft" size="small" @click="copyDraft">复制</el-button>
            </h4>
            <pre v-if="detail.response_draft" class="rw-draft">{{ detail.response_draft }}</pre>
            <p v-else class="rw-muted">该工单暂无应答草稿</p>
          </div>

          <!-- 定责确认 -->
          <div v-if="canDisposition" class="rw-block">
            <h4>定责确认</h4>
            <div class="rw-form">
              <label>定责类型</label>
              <el-select v-model="verdictForm.verdict" placeholder="请选择" style="width: 180px">
                <el-option
                  v-for="opt in ESTABLISHMENT_OPTIONS"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
              <label>改判原因</label>
              <el-input v-model="verdictForm.reason" placeholder="与 AI 不一致时必填" style="width: 260px" />
              <el-button type="primary" :loading="verdictSubmitting" @click="submitVerdict">
                提交定责
              </el-button>
            </div>
          </div>

          <!-- 人工复核 -->
          <div v-if="canReview" class="rw-block">
            <h4>人工复核</h4>
            <div class="rw-form">
              <label>复核动作</label>
              <el-select v-model="reviewForm.action" placeholder="请选择" style="width: 180px">
                <el-option
                  v-for="opt in REVIEW_ACTION_OPTIONS"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
              <label>备注</label>
              <el-input v-model="reviewForm.comment" placeholder="选填" style="width: 220px" />
              <el-button type="primary" :loading="reviewSubmitting" @click="submitReview">
                提交复核
              </el-button>
            </div>
            <p v-if="needReason && !reviewForm.reason.trim()" class="rw-warn">
              该动作需填写理由（下方「理由」必填）
            </p>
            <div v-if="needReason" class="rw-form">
              <label>理由</label>
              <el-input v-model="reviewForm.reason" placeholder="必填" style="width: 380px" />
            </div>
          </div>

          <!-- 回电登记 -->
          <div v-if="canCallback" class="rw-block">
            <h4>回电登记</h4>
            <div class="rw-form">
              <el-input v-model="callbackNote" placeholder="回电结果备注" style="width: 320px" />
              <el-button type="primary" :loading="callbackSubmitting" @click="submitCallback">
                登记回电完成
              </el-button>
            </div>
          </div>

          <el-empty
            v-if="!canDisposition && !canReview && !canCallback"
            description="当前状态无可复核动作"
            :image-size="60"
          />
        </template>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * ComplaintReview：复核工作台（左右分栏独立页）。
 *
 * 与「投诉列表 → 详情」的区别：
 * - 左栏是质检员待办队列，只含待人工复核状态（WAITING_HUMAN_TASK_STATUS），不可改筛选；
 * - 右栏是复核面板，选中即在原地展示证据链/草稿并直接定责/复核/回电，不跳详情页；
 * - 不提供触发研判、建单、演示灌数等接入侧操作。
 *
 * 操作按 task_status 收敛（与 ComplaintDetail 同规则），避免撞后端 A5002。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  listComplaints,
  getComplaint,
  getComplaintTrace,
  submitDispositionVerdict,
  submitDispositionCallback,
  reviewComplaint,
} from '../api'
import type {
  ComplaintDetailResponse,
  ComplaintListItem,
  TaskStatus,
  EstablishmentVerdict,
  ReviewAction,
} from '../types'
import {
  WAITING_HUMAN_TASK_STATUS,
  STATUS_LABELS,
  VERDICT_LABELS,
  ESTABLISHMENT_OPTIONS,
  REVIEW_ACTION_OPTIONS,
  REVIEW_ACTIONS_NEED_REASON,
} from '../types'

const router = useRouter()

const items = ref<ComplaintListItem[]>([])
const listLoading = ref(false)
const listError = ref<Error | null>(null)

const selectedId = ref<string | null>(null)
const detail = ref<ComplaintDetailResponse | null>(null)
const detailLoading = ref(false)
const detailError = ref<Error | null>(null)

const traceItems = ref<{ category: string; name: string; summary: string }[]>([])

const verdictForm = ref<{ verdict: EstablishmentVerdict | ''; reason: string }>({ verdict: '', reason: '' })
const verdictSubmitting = ref(false)

const reviewForm = ref<{ action: ReviewAction | ''; comment: string; reason: string }>({
  action: '', comment: '', reason: '',
})
const reviewSubmitting = ref(false)

const callbackNote = ref('')
const callbackSubmitting = ref(false)

const ts = computed<TaskStatus | null>(() => detail.value?.task_status ?? null)
const canDisposition = computed(() => ts.value === 'pending_review' || ts.value === 'pending_senior_review')
const canReview = computed(() => ts.value === 'pending_review' || ts.value === 'pending_senior_review')
const canCallback = computed(() => ts.value === 'pending_callback')

const chainItems = computed(() => traceItems.value)

const needReason = computed(() =>
  !!reviewForm.value.action && REVIEW_ACTIONS_NEED_REASON.includes(reviewForm.value.action),
)

function shortId(id: string): string {
  return id.length > 22 ? `…${id.slice(-10)}` : id
}

function statusTagType(s: TaskStatus): 'warning' | 'info' | 'success' | 'danger' {
  if (s === 'pending_review' || s === 'pending_senior_review') return 'warning'
  if (s === 'pending_callback') return 'info'
  if (s === 'resolved') return 'success'
  if (s === 'closed' || s === 'archived') return 'danger'
  return 'info'
}

async function fetchQueue(): Promise<void> {
  listLoading.value = true
  listError.value = null
  try {
    const res = await listComplaints({
      page: 1,
      page_size: 100,
      task_status: [...WAITING_HUMAN_TASK_STATUS],
    })
    items.value = res.items
    // 队列刷新后若当前选中项已不在队列中（如已复核完），清空右栏
    if (selectedId.value && !items.value.some((i) => i.complaint_id === selectedId.value)) {
      selectedId.value = null
      detail.value = null
      traceItems.value = []
    }
  } catch (e) {
    listError.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    listLoading.value = false
  }
}

async function selectItem(id: string): Promise<void> {
  selectedId.value = id
  detail.value = null
  traceItems.value = []
  detailError.value = null
  verdictForm.value = { verdict: '', reason: '' }
  reviewForm.value = { action: '', comment: '', reason: '' }
  callbackNote.value = ''
  detailLoading.value = true
  try {
    detail.value = await getComplaint(id)
  } catch (e) {
    detailError.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    detailLoading.value = false
  }
  try {
    traceItems.value = (await getComplaintTrace(id)).events
  } catch {
    traceItems.value = []
  }
}

/** 操作成功后：重拉详情 + 刷新队列（状态可能已移出待复核集合） */
async function afterAction(): Promise<void> {
  if (selectedId.value) await selectItem(selectedId.value)
  await fetchQueue()
}

async function submitVerdict(): Promise<void> {
  if (!selectedId.value || !verdictForm.value.verdict) {
    ElMessage.warning('请先选择定责类型')
    return
  }
  verdictSubmitting.value = true
  try {
    await submitDispositionVerdict(selectedId.value, {
      establishment: verdictForm.value.verdict,
      ...(verdictForm.value.reason.trim() ? { reason: verdictForm.value.reason.trim() } : {}),
    })
    ElMessage.success('定责已提交')
    await afterAction()
  } catch (e) {
    ElMessage.error(`定责提交失败：${e instanceof Error ? e.message : String(e)}`)
  } finally {
    verdictSubmitting.value = false
  }
}

async function submitReview(): Promise<void> {
  if (!selectedId.value || !reviewForm.value.action) {
    ElMessage.warning('请先选择复核动作')
    return
  }
  if (needReason.value && !reviewForm.value.reason.trim()) {
    ElMessage.warning('该复核动作需填写理由')
    return
  }
  reviewSubmitting.value = true
  try {
    await reviewComplaint(selectedId.value, {
      review_action: reviewForm.value.action,
      ...(reviewForm.value.comment.trim() ? { comment: reviewForm.value.comment.trim() } : {}),
      ...(reviewForm.value.reason.trim() ? { reason: reviewForm.value.reason.trim() } : {}),
    })
    ElMessage.success('复核已提交')
    await afterAction()
  } catch (e) {
    ElMessage.error(`复核提交失败：${e instanceof Error ? e.message : String(e)}`)
  } finally {
    reviewSubmitting.value = false
  }
}

async function submitCallback(): Promise<void> {
  if (!selectedId.value) return
  callbackSubmitting.value = true
  try {
    await submitDispositionCallback(selectedId.value, {
      ...(callbackNote.value.trim() ? { note: callbackNote.value.trim() } : {}),
    })
    ElMessage.success('回电已登记')
    await afterAction()
  } catch (e) {
    ElMessage.error(`回电登记失败：${e instanceof Error ? e.message : String(e)}`)
  } finally {
    callbackSubmitting.value = false
  }
}

async function copyDraft(): Promise<void> {
  const text = detail.value?.response_draft
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('草稿已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}

/** 「打开完整详情」= 需要看 Trace 时间线/Agent 日志时再跳全功能页 */
function openFullDetail(): void {
  if (!selectedId.value) return
  void router.push({ path: `/complaints/${selectedId.value}`, query: { focus: 'view' } })
}

onMounted(fetchQueue)
</script>

<style scoped>
.review-workbench {
  display: flex;
  gap: 16px;
  padding: 16px 20px;
  align-items: flex-start;
}
/* 左栏 */
.rw-queue {
  flex: 0 0 340px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}
.rw-queue-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.rw-queue-head h3 { margin: 0; font-size: 15px; flex: 1; }
.rw-count { font-size: 12px; color: #909399; }
.rw-refresh { padding: 2px 10px; font-size: 12px; border: 1px solid #dcdfe6; background: #fff; border-radius: 4px; cursor: pointer; }
.rw-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.rw-item { padding: 10px; border: 1px solid #ebeef5; border-radius: 4px; cursor: pointer; transition: border-color .2s, background .2s; }
.rw-item:hover { border-color: #c6e2ff; background: #f5f9ff; }
.rw-item--active { border-color: #409eff; background: #ecf5ff; }
.rw-item-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.rw-id { font-size: 12px; color: #606266; }
.rw-text { margin: 0 0 6px; font-size: 13px; line-height: 1.5; color: #303133; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.rw-item-bottom { display: flex; justify-content: space-between; font-size: 12px; color: #909399; }
.rw-verdict { color: #e6a23c; }
/* 右栏 */
.rw-panel {
  flex: 1;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 16px;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}
.rw-panel-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 12px; }
.rw-panel-head h3 { margin: 0 0 6px; font-size: 16px; }
.rw-block { margin-bottom: 18px; padding-bottom: 14px; border-bottom: 1px solid #f2f6fc; }
.rw-block:last-child { border-bottom: none; }
.rw-block h4 { margin: 0 0 8px; font-size: 14px; display: flex; align-items: center; gap: 8px; color: #303133; }
.rw-raw { margin: 0; font-size: 13px; line-height: 1.7; color: #303133; background: #fafafa; padding: 10px; border-radius: 4px; }
.rw-kv { display: grid; grid-template-columns: 90px 1fr 90px 1fr; gap: 8px 12px; font-size: 13px; }
.rw-kv span { color: #909399; }
.rw-evidence { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
.rw-evidence li { font-size: 13px; line-height: 1.6; }
.rw-ev-tag { margin-right: 8px; }
.rw-ev-summary { color: #606266; margin-left: 8px; }
.rw-draft { margin: 0; background: #f5f7fa; border-left: 3px solid #409eff; padding: 12px; border-radius: 4px; font-size: 13px; line-height: 1.7; white-space: pre-wrap; }
.rw-muted { margin: 0; font-size: 13px; color: #909399; }
.rw-form { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.rw-form label { font-size: 13px; color: #606266; }
.rw-warn { margin: 0 0 8px; font-size: 12px; color: #e6a23c; }
</style>
