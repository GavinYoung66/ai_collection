<script setup lang="ts">
/**
 * 投诉详情页（批次 3.1–3.4）。
 *
 * 核心约束：
 * - 状态步骤条枚举取 types.ts.TaskStatus，前端不发明状态
 * - 轮询只由 task_status 驱动（D2），judge 成败不控制轮询
 * - 关单等操作前端不做自造状态机校验（SSOT §14.1），只调 api 让后端返回错误
 * - 错误码按白名单 code 分派模块侧动作，不看 HTTP 状态
 * - X-Trace-Id 上下文由 useTraceContext（T5）负责，本文件不手写请求头
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getComplaint,
  getComplaintVerdict,
  getComplaintTrace,
  getComplaintAgentLogs,
  judgeComplaint,
  submitDispositionVerdict,
  submitDispositionCallback,
  reviewComplaint,
  closeComplaint,
} from '../api'
import type {
  AgentLogItem,
  ComplaintDetailResponse,
  DispositionVerdictRequest,
  EstablishmentVerdict,
  ReviewAction,
  ReviewRequest,
  TaskStatus,
  TraceEventItem,
  VerdictResponse,
} from '../types'
import { WAITING_HUMAN_TASK_STATUS, VERDICT_LABELS, ESTABLISHMENT_OPTIONS } from '../types'
import { usePolling } from '../composables/usePolling'
import { useTraceContext } from '../composables/useTraceContext'
import TraceIdText from '@/shared/components/base/TraceIdText.vue'
import EvidencePanel from '../components/EvidencePanel.vue'
import TraceTimeline from '../components/TraceTimeline.vue'
import AgentLogs from '../components/AgentLogs.vue'
import DraftPanel from '../components/DraftPanel.vue'

defineOptions({ name: 'ComplaintDetail' })

// T5：trace 上下文（onMounted 生成写入，onUnmounted 自动清空）
useTraceContext()

const route = useRoute()
const router = useRouter()
const complaintId = computed(() => route.params.id as string)

// ── 状态步骤条配置（TaskStatus 枚举，前端不发明） ──
const STATUS_STEPS: { status: TaskStatus; label: string }[] = [
  { status: 'pending', label: '待处理' },
  { status: 'evidence_gathering', label: '证据收集' },
  { status: 'reflection_retry', label: '反思重试' },
  { status: 'judging', label: '研判中' },
  { status: 'draft_generated', label: '草稿生成' },
  { status: 'pending_review', label: '待人工复核' },
  { status: 'pending_senior_review', label: '待高级复核' },
  { status: 'pending_callback', label: '待回电' },
  { status: 'resolved', label: '已解决' },
  { status: 'closed', label: '已关单' },
  { status: 'archived', label: '已归档' },
]

// ── 详情状态 ──
const loading = ref(false)
const detail = ref<ComplaintDetailResponse | null>(null)
const verdict = ref<VerdictResponse | null>(null)
const errorMessage = ref<string | null>(null)
const errorCode = ref<string | null>(null)
const errorTrace = ref<string | null>(null)

const isNotFound = computed(() => errorCode.value === 'A5001')
const currentStepIndex = computed(() => {
  if (!detail.value) return -1
  return STATUS_STEPS.findIndex((s) => s.status === detail.value!.task_status)
})
const verdictChanged = computed(() => {
  if (!detail.value) return false
  const ai = detail.value.ai_verdict
  const human = detail.value.disposition_verdict
  return ai !== null && human !== null && ai !== human
})

// ── 操作可用性（按 task_status 收敛，避免点错撞后端 A5002） ──
// 后端约束（complaint_service.py / review_service.py）：
//   定责 disposition/verdict → 仅 pending_review / pending_senior_review
//   回电 disposition/callback → 仅 pending_callback（→ resolved）
//   关单 close → 仅 resolved
const ts = computed<TaskStatus | null>(() => detail.value?.task_status ?? null)
const JUDGE_READY: readonly TaskStatus[] = [
  'pending', 'evidence_gathering', 'reflection_retry', 'judging', 'draft_generated',
  'pending_review', 'pending_senior_review',
]
const REVIEW_READY: readonly TaskStatus[] = ['pending_review', 'pending_senior_review']
const canJudge = computed(() => !!ts.value && JUDGE_READY.includes(ts.value))
const canDisposition = computed(() => !!ts.value && REVIEW_READY.includes(ts.value))
const canReview = computed(() => !!ts.value && REVIEW_READY.includes(ts.value))
const canCallback = computed(() => ts.value === 'pending_callback')
const canClose = computed(() => ts.value === 'resolved')
const hasAnyAction = computed(() =>
  canJudge.value || canDisposition.value || canReview.value || canCallback.value || canClose.value,
)

// 证据链规整（后端 ordered_evidence 为 Record，此处归一化供模板直接渲染）
const evidenceList = computed(() =>
  (detail.value?.ordered_evidence ?? []).map((e) => ({
    category: String(e.category ?? '其他'),
    name: String(e.name ?? '—'),
    summary: String(e.summary ?? ''),
  })),
)

// AI 取证链：来自 Trace 事件（详情加载后即拉取，不必等用户切 Tab）
const chainItems = computed(() =>
  traceEvents.value.map((e) => ({
    category: String(e.category ?? '其他'),
    name: String(e.name ?? '—'),
    summary: String(e.summary ?? ''),
  })),
)

async function loadAiTrace(): Promise<void> {
  const id = complaintId.value
  if (!id) return
  traceLoading.value = true
  try { traceEvents.value = (await getComplaintTrace(id)).events } catch { traceEvents.value = [] }
  finally { traceLoading.value = false }
  tabLoaded.value.trace = true
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

// ── 入口锚点定位：列表「研判」→ ?focus=judge，复核工作台「复核」→ ?focus=review ──
// 详情页据 focus 滚动到对应操作块并高亮 2s（不自动调接口，避免误触发 LLM 成本）
const focusTarget = ref<string | null>(null)
function applyFocus(): void {
  const focus = route.query.focus
  if (typeof focus !== 'string' || !focus || focus === 'view') return
  const anchorMap: Record<string, string> = {
    judge: 'op-judge',
    disposition: 'op-disposition',
    review: 'op-review',
    callback: 'op-callback',
  }
  const el = document.getElementById(anchorMap[focus] ?? '')
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  focusTarget.value = focus
  window.setTimeout(() => { focusTarget.value = null }, 2000)
}

// ── Tab 组：首次切换才发请求 ──
type TabKey = 'evidence' | 'trace' | 'agent-logs' | 'draft'
const activeTab = ref<TabKey>('evidence')
const tabLoaded = ref<Record<TabKey, boolean>>({
  evidence: false, trace: false, 'agent-logs': false, draft: false,
})
const traceEvents = ref<TraceEventItem[]>([])
const agentLogItems = ref<AgentLogItem[]>([])
const traceLoading = ref(false)
const agentLogsLoading = ref(false)

async function onTabChange(tab: TabKey): Promise<void> {
  activeTab.value = tab
  if (tabLoaded.value[tab]) return
  const id = complaintId.value
  if (!id) return
  tabLoaded.value[tab] = true
  if (tab === 'trace') {
    traceLoading.value = true
    try { traceEvents.value = (await getComplaintTrace(id)).events } catch { traceEvents.value = [] }
    finally { traceLoading.value = false }
  } else if (tab === 'agent-logs') {
    agentLogsLoading.value = true
    try { agentLogItems.value = (await getComplaintAgentLogs(id)).logs } catch { agentLogItems.value = [] }
    finally { agentLogsLoading.value = false }
  }
}

// ── 加载详情（含错误码模块侧动作） ──
async function loadDetail(): Promise<void> {
  const id = complaintId.value
  if (!id) { errorMessage.value = '缺少工单 ID'; return }
  loading.value = true
  errorMessage.value = null; errorCode.value = null; errorTrace.value = null
  try {
    detail.value = await getComplaint(id)
    verdict.value = await getComplaintVerdict(id).catch(() => null)
  } catch (err: unknown) {
    const e = err as { message?: string; code?: string; trace?: string }
    errorMessage.value = e?.message ?? '加载失败'
    errorCode.value = e?.code ?? null
    errorTrace.value = e?.trace ?? null
    // A5001 → 404 空态（computed isNotFound 驱动）
  } finally { loading.value = false }
}

// ── 错误码模块侧动作（白名单 code 判定，不看 HTTP 状态） ──
function handleBizError(e: { message?: string; code?: string; trace?: string }, defaultAction = 'error'): void {
  const code = e.code ?? null
  const message = e.message ?? '操作失败'
  const trace = e.trace ?? null
  const traceTail = trace ? trace.slice(-8) : '无'

  switch (code) {
    case 'A5001':
      // 工单不存在 → 404 空态 + 返回列表
      errorCode.value = 'A5001'
      errorMessage.value = message
      errorTrace.value = trace
      break
    case 'A5002':
      // 状态已变更 → 提示 + 自动刷新
      ElMessage.info(`${message}（trace: ${traceTail}）`)
      void loadDetail()
      break
    case 'B0005':
      // 幂等（如重复回电登记）→ info 样式
      ElMessage.info(`${message}（trace: ${traceTail}）`)
      break
    case 'B0010':
      // 合规拦截 → 强提示 + 不重试
      void ElMessageBox.alert(`${message}\n\n（trace: ${traceTail}）`, '操作被拦截', { type: 'error', confirmButtonText: '我知道了' })
      break
    case 'A5015': case 'A5016': case 'A5017':
      // 已转人工审核 → 正向提示 + 引导跳复核
      ElMessage.success(`${message}（trace: ${traceTail}）`)
      void router.push('/complaints/review')
      break
    default:
      if (defaultAction === 'error') {
        ElMessage.error(`${message}（trace: ${traceTail}）`)
      } else {
        ElMessage.success(`${message}（trace: ${traceTail}）`)
      }
  }
}

// ── 轮询集成（只由 task_status 驱动，D2 解耦） ──
const { isPolling, start: startPolling, stop: stopPolling } = usePolling(
  computed(() => complaintId.value || ''),
  {
    onTerminalStop: async () => {
      // 终态 → 补拉 verdict + agent-logs + 刷新详情
      const id = complaintId.value
      if (!id) return
      try { verdict.value = await getComplaintVerdict(id) } catch { /* ignore */ }
      try { agentLogItems.value = (await getComplaintAgentLogs(id)).logs } catch { /* ignore */ }
      await loadDetail()
    },
  },
)

function maybeStartPolling(): void {
  const ts = detail.value?.task_status
  if (!ts) return
  if (ts === 'judging' || (WAITING_HUMAN_TASK_STATUS as readonly string[]).includes(ts)) {
    void startPolling()
  }
}

// ── 操作组（D2：judge 成败不控制轮询，轮询只由 task_status 驱动） ──
const judgeLoading = ref(false)
async function handleJudge(): Promise<void> {
  const id = complaintId.value
  if (!id) return
  judgeLoading.value = true
  try {
    await judgeComplaint(id)
    ElMessage.success('研判已触发')
    // 不手动启动轮询——onTabChange/操作后 loadDetail 会根据 task_status 自动启动
    await loadDetail()
    maybeStartPolling()
  } catch (err: unknown) {
    // judge 失败只提示（D2 不控制轮询）
    handleBizError(err as { message?: string; code?: string; trace?: string })
  } finally { judgeLoading.value = false }
}

const dispositionLoading = ref(false)
const dispositionForm = ref<{ verdict: EstablishmentVerdict | ''; reason: string }>({ verdict: '', reason: '' })
async function handleDisposition(): Promise<void> {
  const id = complaintId.value
  const curDetail = detail.value
  if (!id || !curDetail) return
  const v = dispositionForm.value.verdict
  if (!v) { ElMessage.warning('请选择定责类型'); return }
  // 禁止提交 pending
  if ((v as string) === 'pending') { ElMessage.error('禁止提交 pending 状态'); return }
  // 与 AI 初选不一致时改判原因必填
  const ai = curDetail.ai_verdict
  const verdictVal = v as EstablishmentVerdict
  if (ai !== null && ai !== verdictVal && !dispositionForm.value.reason.trim()) {
    ElMessage.warning('与 AI 初选不一致时，改判原因必填')
    return
  }
  dispositionLoading.value = true
  try {
    const body: DispositionVerdictRequest = {
      establishment: verdictVal,
      reason: dispositionForm.value.reason.trim() || undefined,
    }
    await submitDispositionVerdict(id, body)
    ElMessage.success('定责已提交')
    dispositionForm.value = { verdict: '', reason: '' }
    await loadDetail()
  } catch (err: unknown) {
    handleBizError(err as { message?: string; code?: string; trace?: string })
  } finally { dispositionLoading.value = false }
}

const callbackLoading = ref(false)
async function handleCallback(): Promise<void> {
  const id = complaintId.value
  if (!id) return
  callbackLoading.value = true
  try {
    await submitDispositionCallback(id)
    ElMessage.success('回电登记成功')
    await loadDetail()
  } catch (err: unknown) {
    // B0005 按幂等 info 提示
    handleBizError(err as { message?: string; code?: string; trace?: string })
  } finally { callbackLoading.value = false }
}

const reviewLoading = ref(false)
const reviewForm = ref<{ action: ReviewAction | ''; reason: string }>({ action: '', reason: '' })
async function handleReview(): Promise<void> {
  const id = complaintId.value
  if (!id) return
  const action = reviewForm.value.action
  if (!action) { ElMessage.warning('请选择复核动作'); return }
  reviewLoading.value = true
  try {
    const body: ReviewRequest = {
      review_action: action as ReviewAction,
      reason: reviewForm.value.reason.trim() || undefined,
    }
    await reviewComplaint(id, body)
    ElMessage.success('复核已提交')
    reviewForm.value = { action: '', reason: '' }
    await loadDetail()
    maybeStartPolling()
  } catch (err: unknown) {
    handleBizError(err as { message?: string; code?: string; trace?: string })
  } finally { reviewLoading.value = false }
}

const closeLoading = ref(false)
async function handleClose(): Promise<void> {
  const id = complaintId.value
  if (!id) return
  closeLoading.value = true
  try {
    await closeComplaint(id)
    ElMessage.success('工单已关单')
    await loadDetail()
    stopPolling()
  } catch (err: unknown) {
    // 关单失败原地展示 message + trace；前端不做自造状态机校验
    handleBizError(err as { message?: string; code?: string; trace?: string })
  } finally { closeLoading.value = false }
}

async function handleRejudge(): Promise<void> { await handleJudge() }

// ── 生命周期 ──
onMounted(async () => {
  await loadDetail()
  void loadAiTrace()
  maybeStartPolling()
  await nextTick()
  applyFocus()
})

// complaintId 变化（深链到另一工单）→ 重启轮询
watch(complaintId, async (newId, oldId) => {
  if (newId === oldId) return
  stopPolling()
  await loadDetail()
  maybeStartPolling()
})

onUnmounted(() => { stopPolling() })
</script>

<template>
  <div class="complaint-detail">
    <!-- 加载态 -->
    <el-skeleton v-if="loading" :rows="6" animated />

    <!-- A5001 工单不存在 → 404 空态 -->
    <div v-else-if="isNotFound" class="not-found">
      <el-empty description="投诉工单不存在">
        <el-button type="primary" @click="router.push('/complaints')">返回列表</el-button>
      </el-empty>
    </div>

    <!-- 通用错误态（非 A5001） -->
    <div v-else-if="errorMessage" class="error-state">
      <el-alert type="error" :title="errorMessage" show-icon>
        <template #default>
          <span v-if="errorTrace">trace: </span>
          <TraceIdText v-if="errorTrace" :trace-id="errorTrace" />
        </template>
      </el-alert>
    </div>

    <!-- 正常态 -->
    <template v-else-if="detail">
      <header class="detail-header">
        <div class="complaint-meta">
          <span class="complaint-id">工单 #{{ detail.complaint_id }}</span>
          <el-tag :type="currentStepIndex >= 8 ? 'success' : currentStepIndex >= 5 ? 'warning' : 'info'" size="small">
            {{ STATUS_STEPS[currentStepIndex]?.label ?? detail.task_status }}
          </el-tag>
          <span v-if="isPolling" class="polling-indicator">● 轮询中</span>
        </div>
        <p class="complaint-text">{{ detail.complaint_text }}</p>
      </header>

      <!-- ① 状态步骤条 -->
      <section class="step-bar">
        <el-steps :active="currentStepIndex" align-center finish-status="success" process-status="primary">
          <el-step v-for="step in STATUS_STEPS" :key="step.status" :title="step.label" />
        </el-steps>
      </section>

      <!-- ② AI vs 人审对照 -->
      <section class="verdict-compare">
        <h3>定责对照</h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="AI 初选">{{ detail.ai_verdict ? VERDICT_LABELS[detail.ai_verdict] : '—' }}</el-descriptions-item>
          <el-descriptions-item label="人审定责">
            <span :class="{ 'verdict-changed': verdictChanged }">{{ detail.disposition_verdict ? VERDICT_LABELS[detail.disposition_verdict] : '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="verdictChanged" label="改判原因" :span="2">
            {{ detail.disposition_reason ?? '（未填写）' }}
          </el-descriptions-item>
        </el-descriptions>
      </section>

      <!-- ③ 结论卡 -->
      <section class="verdict-card" v-if="verdict">
        <h3>研判结论</h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="判责类型">{{ verdict.verdict ? VERDICT_LABELS[verdict.verdict] : '—' }}</el-descriptions-item>
          <el-descriptions-item label="置信度">
            {{ verdict.confidence != null ? `${(verdict.confidence * 100).toFixed(1)}%` : '—' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="verdict.rag_source_ids.length" label="RAG 源" :span="2">
            <el-tag v-for="sid in verdict.rag_source_ids" :key="sid" size="small" style="margin-right: 4px">{{ sid }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </section>

      <!-- ④ AI 产出物：取证链 + 违规清单（定责后）+ 应答草稿，直接展示不必翻 Tab -->
      <section v-if="chainItems.length || evidenceList.length || detail.response_draft" class="ai-output">
        <h3>AI 产出物</h3>

        <!-- 取证链：AI 调用了哪些工具、拿到什么（来自 trace） -->
        <div v-if="chainItems.length" class="ai-output-block">
          <h4>AI 取证链（{{ chainItems.length }} 条）</h4>
          <ol class="ev-list">
            <li v-for="(ev, i) in chainItems" :key="`c-${i}`">
              <el-tag size="small" class="ev-cat">{{ ev.category }}</el-tag>
              <strong class="ev-name">{{ ev.name }}</strong>
              <span class="ev-summary">{{ ev.summary }}</span>
            </li>
          </ol>
        </div>

        <!-- 违规清单：仅人审定责=坐实违规后由后端生成 -->
        <div v-if="evidenceList.length" class="ai-output-block">
          <h4>违规清单（{{ evidenceList.length }} 条，已定责）</h4>
          <ol class="ev-list">
            <li v-for="(ev, i) in evidenceList" :key="`e-${i}`">
              <el-tag size="small" class="ev-cat">{{ ev.category }}</el-tag>
              <strong class="ev-name">{{ ev.name }}</strong>
              <span class="ev-summary">{{ ev.summary }}</span>
            </li>
          </ol>
        </div>

        <!-- 应答草稿 -->
        <div v-if="detail.response_draft" class="ai-output-block">
          <h4>
            应答草稿
            <el-button size="small" class="copy-btn" @click="copyDraft">复制草稿</el-button>
          </h4>
          <pre class="draft-pre">{{ detail.response_draft }}</pre>
        </div>

        <!-- 定责前提示：违规清单待定责后生成 -->
        <el-alert
          v-if="canDisposition && !evidenceList.length"
          type="info"
          :closable="false"
          show-icon
          title="违规清单将在「定责为坐实违规」后生成；当前可先查看上方 AI 取证链与 Trace 时间线。"
        />
      </section>

      <!-- 操作组：按 task_status 显隐（只展示当前阶段可做的操作） -->
      <section class="operations">
        <h3>操作</h3>

        <!-- 研判：pending ~ pending_senior_review -->
        <div
          v-if="canJudge"
          id="op-judge"
          class="op-block"
          :class="{ 'op-focus': focusTarget === 'judge' }"
        >
          <div class="operation-row">
            <el-button
              type="primary"
              :loading="judgeLoading"
              :disabled="detail.task_status === 'judging' || detail.task_status === 'draft_generated'"
              @click="handleJudge"
            >
              触发研判
            </el-button>
            <el-button :loading="judgeLoading" :disabled="detail.task_status === 'judging'" @click="handleRejudge">
              重新研判
            </el-button>
          </div>
        </div>

        <!-- 定责确认：仅 pending_review / pending_senior_review -->
        <div
          v-if="canDisposition"
          id="op-disposition"
          class="op-block"
          :class="{ 'op-focus': focusTarget === 'disposition' }"
        >
          <el-divider content-position="left">定责确认</el-divider>
          <el-form :model="dispositionForm" label-width="100px" inline>
            <el-form-item label="定责类型">
              <el-select v-model="dispositionForm.verdict" placeholder="请选择" style="width: 220px">
                <el-option
                  v-for="opt in ESTABLISHMENT_OPTIONS"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="改判原因">
              <el-input v-model="dispositionForm.reason" placeholder="AI 不一致时必填" style="width: 280px" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="dispositionLoading" @click="handleDisposition">提交定责</el-button>
            </el-form-item>
          </el-form>
        </div>

        <!-- 人工复核：仅 pending_review / pending_senior_review -->
        <div
          v-if="canReview"
          id="op-review"
          class="op-block"
          :class="{ 'op-focus': focusTarget === 'review' }"
        >
          <el-divider content-position="left">人工复核</el-divider>
        <el-form :model="reviewForm" label-width="100px" inline>
          <el-form-item label="复核动作">
            <el-select v-model="reviewForm.action" placeholder="请选择" style="width: 220px">
              <el-option label="approve（通过）" value="approve" />
              <el-option label="modify_verdict（改判）" value="modify_verdict" />
              <el-option label="modify_response（改回应）" value="modify_response" />
              <el-option label="reject_rejudge（驳回重研判）" value="reject_rejudge" />
              <el-option label="add_evidence（补充证据）" value="add_evidence" />
              <el-option label="escalate（升级）" value="escalate" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="reviewLoading" @click="handleReview">提交复核</el-button>
          </el-form-item>
        </el-form>
        </div>

        <!-- 回电与关单：回电仅 pending_callback，关单仅 resolved -->
        <div
          v-if="canCallback || canClose"
          id="op-callback"
          class="op-block"
          :class="{ 'op-focus': focusTarget === 'callback' }"
        >
          <el-divider content-position="left">回电与关单</el-divider>
          <div class="operation-row">
            <el-button v-if="canCallback" :loading="callbackLoading" @click="handleCallback">
              回电登记
            </el-button>
            <el-button v-if="canClose" type="danger" :loading="closeLoading" @click="handleClose">
              关单
            </el-button>
          </div>
        </div>

        <!-- 终态（已关单/已归档/待回电外的不可操作态）提示 -->
        <el-empty
          v-if="!hasAnyAction"
          description="当前状态无可用操作"
          :image-size="60"
        />
      </section>

      <!-- Tab 组：证据包/草稿在标签上带条目提示，避免"看不到内容"的误判 -->
      <section class="detail-tabs">
        <el-tabs v-model="activeTab" @tab-change="onTabChange">
          <el-tab-pane :label="`证据包 (${(detail.ordered_evidence ?? []).length})`" name="evidence">
            <EvidencePanel :evidence="detail.ordered_evidence ?? []" :refs="detail.evidence_refs ?? []" />
          </el-tab-pane>
          <el-tab-pane label="Trace 时间线" name="trace">
            <el-skeleton v-if="traceLoading" :rows="3" animated />
            <TraceTimeline v-else :events="traceEvents" />
          </el-tab-pane>
          <el-tab-pane label="Agent 日志" name="agent-logs">
            <el-skeleton v-if="agentLogsLoading" :rows="3" animated />
            <AgentLogs v-else :logs="agentLogItems" />
          </el-tab-pane>
          <el-tab-pane :label="detail.response_draft ? '响应草稿 ✓' : '响应草稿'" name="draft">
            <DraftPanel :draft="detail.response_draft" />
          </el-tab-pane>
        </el-tabs>
      </section>
    </template>
  </div>
</template>

<style scoped>
.complaint-detail { padding: 16px; display: flex; flex-direction: column; gap: 24px; }
.detail-header { display: flex; flex-direction: column; gap: 8px; }
.complaint-meta { display: flex; align-items: center; gap: 12px; }
.complaint-id { font-weight: 600; font-family: monospace; }
.polling-indicator { color: #67c23a; font-size: 12px; }
.complaint-text { background: #f5f7fa; padding: 12px; border-radius: 4px; margin: 0; line-height: 1.6; }
.step-bar { padding: 16px 0; overflow-x: auto; }
h3 { margin: 0 0 12px; font-size: 16px; color: #303133; }
.verdict-changed { color: #e6a23c; font-weight: 600; }
.operation-row { display: flex; gap: 12px; }
/* 入口锚点高亮（?focus=judge|review 进入后 2s） */
.op-block { border-radius: 4px; transition: outline-color .3s; }
.op-focus { outline: 2px solid #409eff; outline-offset: 4px; }
/* AI 产出物：证据链 + 应答草稿 */
.ai-output-block { margin-bottom: 12px; }
.ai-output-block h4 { margin: 0 0 8px; font-size: 14px; display: flex; align-items: center; gap: 8px; }
.ev-list { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
.ev-list li { font-size: 13px; line-height: 1.6; }
.ev-cat { margin-right: 8px; }
.ev-name { margin-right: 8px; }
.ev-summary { color: #606266; }
.draft-pre {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 4px;
  border-left: 3px solid #409eff;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  margin: 0;
}
.not-found { padding: 48px 0; }
.error-state { padding: 16px 0; }
</style>
