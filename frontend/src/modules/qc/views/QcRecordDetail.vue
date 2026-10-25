<script setup lang="ts">
/**
 * QcRecordDetail：质检报告详情（f2-qc-console 批次4 / tasks 5.2-5.5）。
 *
 * 规格（spec「详情页必须呈现扣分、命中、条款、skip 与 trace」）：
 * - 头部展示 deduction_total/compliance_score/risk/qc_status/qc_result，评级字段已退役不展示；
 * - 命中表：来源 ac|rule|llm、rule_id 或词、证据、本条扣分 points（high 20/medium 8/low 2），
 *   客户侧（score_eligible=false，含 R13）标注「客户侧不计入」、points 记 0 不进扣分合计；
 * - 制度依据（policy_clause/policy_text）与 kb_snippet 严格分区：kb_snippet 独立区块展示，
 *   不与 policy_text 混用；
 * - skipped_rules 文案为「本通未审/未适用」，不得称「已合规」；
 * - 会话证据对照：按命中逐条呈现说话方/轮次/证据原文，命中词 <mark> 高亮
 *   （契约无整通 transcript 字段，以脱敏后的 evidence_text 为原文片段，旧页对照语义照此落地）；
 * - trace 时间线取 demo/{session_id}/trace（模块内自建 TraceTimeline，design D4），
 *   仅当响应含 langsmith_url 时显示外链；
 * - 内嵌人审按钮组 ReviewActions（CONFIRM/CORRECT 完整逻辑，批次5 QcReview 复用）；
 *   当前角色 ∉ {admin, qc_reviewer, supervisor} 时置灰 + 说明（角色经 useCurrentRole inject 桥取）；
 * - trace 上下文：挂载写入、卸载清空（useQcTraceContext，design D5），业务代码不手写 X-Trace-Id；
 * - B0004/A2005 → 空态 + 返回列表（§15.1 白名单动作）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { RequestError } from '@/shared/request'
import { TraceIdText } from '@/shared'
import { getQcDemoTrace, getQcRecord } from '../api'
import type {
  QcHit,
  QcRecordDetailResponse,
  QcResult,
  QcRiskLevel,
  QcStatus,
  QcTraceResponse,
} from '../types'
import { QC_PENALTY_POINTS, QC_R13_RULE_ID } from '../types'
import { useQcTraceContext } from '../composables/useQcTraceContext'
import TraceTimeline from '../components/TraceTimeline.vue'
import ReviewActions from '../components/ReviewActions.vue'

defineOptions({ name: 'QcRecordDetail' })

// design D5：详情页挂载写入 trace 上下文，卸载自动清空；拦截器统一注入 X-Trace-Id
useQcTraceContext()

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => route.params.sessionId as string)

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
const SOURCE_LABELS: Record<string, string> = {
  ac: 'AC 词库',
  rule: '规则引擎',
  llm: 'LLM 复核',
}
const SPEAKER_LABELS: Record<string, string> = {
  agent: '坐席',
  user: '客户',
}

// ── 详情状态 ──
const loading = ref(false)
const detail = ref<QcRecordDetailResponse | null>(null)
const errorMessage = ref<string | null>(null)
const errorTrace = ref<string | null>(null)
const notFound = ref(false)

// ── trace 状态 ──
const trace = ref<QcTraceResponse | null>(null)
const traceLoading = ref(false)
const traceError = ref<string | null>(null)

/** 含 kb_snippet 的命中（独立区块展示，绝不与 policy_text 混用） */
const kbHits = computed(() => (detail.value?.hits ?? []).filter((h) => !!h.kb_snippet))

/** 本条扣分展示口径：客户侧（score_eligible=false）记 0 标注不计入；
 *  坐席侧 points 为空时按档位兜底（high 20/medium 8/low 2，types.ts PENALTY 注释） */
function pointsLabel(hit: QcHit): string {
  if (!hit.score_eligible) return '不计入'
  const points = hit.points ?? QC_PENALTY_POINTS[hit.severity]
  return `-${points}`
}

/** 客户侧（含 R13 停止通话）标注文案 */
function notCountedLabel(hit: QcHit): string {
  return hit.rule_id === QC_R13_RULE_ID ? '客户侧 R13 不计入' : '客户侧不计入'
}

/** 证据原文按命中词切分（对照高亮用）；word 为空则整段不高亮 */
interface EvidencePart {
  text: string
  hit: boolean
}
function highlightParts(text: string, word: string | null): EvidencePart[] {
  const w = (word ?? '').trim()
  if (!text || !w) return [{ text, hit: false }]
  const parts: EvidencePart[] = []
  let cursor = 0
  for (;;) {
    const found = text.indexOf(w, cursor)
    if (found === -1) {
      parts.push({ text: text.slice(cursor), hit: false })
      break
    }
    if (found > cursor) parts.push({ text: text.slice(cursor, found), hit: false })
    parts.push({ text: w, hit: true })
    cursor = found + w.length
  }
  return parts
}

/** 证据对照行模型 */
const transcriptRows = computed(() =>
  (detail.value?.hits ?? []).map((hit) => ({
    hit,
    parts: highlightParts(hit.evidence_text, hit.word),
  })),
)

async function loadDetail(): Promise<void> {
  const id = sessionId.value
  if (!id) {
    errorMessage.value = '缺少会话 ID'
    return
  }
  loading.value = true
  errorMessage.value = null
  errorTrace.value = null
  notFound.value = false
  try {
    detail.value = await getQcRecord(id)
    // 详情就绪后再拉 trace；trace 失败不阻断详情展示
    void loadTrace(id)
  } catch (e) {
    const err = e as RequestError
    // §15.1 白名单：B0004 详情空态 + 返回列表（A2005 为后端详情 404 的实际码，同动作）
    if (err.code === 'B0004' || err.code === 'A2005') {
      notFound.value = true
    } else {
      errorMessage.value = err.message ?? '加载失败'
      errorTrace.value = err.trace ?? null
    }
  } finally {
    loading.value = false
  }
}

async function loadTrace(id: string): Promise<void> {
  traceLoading.value = true
  traceError.value = null
  try {
    trace.value = await getQcDemoTrace(id)
  } catch (e) {
    trace.value = null
    traceError.value = (e as Error).message ?? 'trace 加载失败'
  } finally {
    traceLoading.value = false
  }
}

/** 人审提交成功 → 重拉详情（qc_status 进入 REVIEWED，按钮组随之禁用） */
async function onReviewSubmitted(): Promise<void> {
  ElMessage.success('已刷新质检结果')
  await loadDetail()
}

function backToList(): void {
  void router.push('/qc/records')
}

onMounted(() => {
  void loadDetail()
})
</script>

<template>
  <div class="qc-record-detail">
    <header class="qrd-header">
      <button
        type="button"
        class="qrd-btn"
        @click="backToList"
      >
        ← 返回列表
      </button>
      <h2 class="qrd-title">
        报告详情 <code>{{ sessionId }}</code>
      </h2>
    </header>

    <!-- 加载态 -->
    <div
      v-if="loading"
      class="qrd-skeleton"
    >
      加载中…
    </div>

    <!-- B0004/A2005：空态 + 返回列表（§15.1 白名单动作） -->
    <div
      v-else-if="notFound"
      class="qrd-empty"
    >
      <p>质检记录不存在或已被清理</p>
      <button
        type="button"
        class="qrd-btn qrd-btn--primary"
        @click="backToList"
      >
        返回列表
      </button>
    </div>

    <!-- 通用错误态：message 直出 + trace 后 8 位 + 重试 -->
    <div
      v-else-if="errorMessage"
      class="qrd-error"
      role="alert"
    >
      <span>{{ errorMessage }}</span>
      <TraceIdText
        v-if="errorTrace"
        :trace-id="errorTrace"
      />
      <button
        type="button"
        class="qrd-btn"
        @click="loadDetail"
      >
        重试
      </button>
    </div>

    <template v-else-if="detail">
      <!-- 头部：扣分/合规分/风险/状态/结论（评级字段已退役不展示） -->
      <section class="qrd-meta">
        <dl class="qrd-kv">
          <div class="qrd-kv-item">
            <dt>扣分合计</dt>
            <dd class="qrd-deduction">
              -{{ detail.deduction_total }}
            </dd>
          </div>
          <div class="qrd-kv-item">
            <dt>合规分</dt>
            <dd>{{ detail.compliance_score }}</dd>
          </div>
          <div class="qrd-kv-item">
            <dt>风险</dt>
            <dd>{{ RISK_LABELS[detail.risk] }}</dd>
          </div>
          <div class="qrd-kv-item">
            <dt>质检状态</dt>
            <dd>{{ QC_STATUS_LABELS[detail.qc_status] }}</dd>
          </div>
          <div class="qrd-kv-item">
            <dt>质检结论</dt>
            <dd>
              <span
                class="qrd-tag"
                :class="`qrd-tag--${detail.qc_result}`"
              >{{ QC_RESULT_LABELS[detail.qc_result] }}</span>
            </dd>
          </div>
          <div class="qrd-kv-item">
            <dt>命中统计</dt>
            <dd>高 {{ detail.high_count }} / 中 {{ detail.med_count }} / 低 {{ detail.low_count }}</dd>
          </div>
          <div class="qrd-kv-item">
            <dt>案件 ID</dt>
            <dd><code>{{ detail.case_id ?? '—' }}</code></dd>
          </div>
          <div class="qrd-kv-item">
            <dt>坐席 ID</dt>
            <dd><code>{{ detail.agent_id ?? '—' }}</code></dd>
          </div>
        </dl>
      </section>

      <!-- 内嵌人审按钮组（批次5 QcReview 页复用同一组件） -->
      <section class="qrd-section">
        <h3>人工复核</h3>
        <ReviewActions
          :qc-record-id="detail.qc_record_id"
          :deduction-total="detail.deduction_total"
          :qc-status="detail.qc_status"
          @submitted="onReviewSubmitted"
        />
      </section>

      <!-- 命中表：来源/规则或词/证据/本条扣分/制度依据 -->
      <section class="qrd-section">
        <h3>命中表（{{ detail.hits.length }} 条）</h3>
        <p
          v-if="!detail.hits.length"
          class="qrd-muted"
        >
          无命中
        </p>
        <table
          v-else
          class="qrd-table qc-hits"
        >
          <thead>
            <tr>
              <th>来源</th>
              <th>规则/词</th>
              <th>证据</th>
              <th>本条扣分</th>
              <th>制度依据</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(hit, i) in detail.hits"
              :key="i"
              :data-rule-id="hit.rule_id ?? undefined"
            >
              <td>{{ hit.source ? (SOURCE_LABELS[hit.source] ?? hit.source) : '—' }}</td>
              <td><code>{{ hit.rule_id ?? hit.word ?? '—' }}</code></td>
              <td>{{ hit.evidence_text || '—' }}</td>
              <td class="qc-hit-points">
                <span>{{ pointsLabel(hit) }}</span>
                <span
                  v-if="!hit.score_eligible"
                  class="qc-hit-not-counted"
                >{{ notCountedLabel(hit) }}</span>
              </td>
              <!-- 制度依据：仅静态制度条款（policy_clause/policy_text）；kb_snippet 走下方独立区块 -->
              <td class="qc-hit-policy">
                <template v-if="hit.policy_clause || hit.policy_text">
                  <code v-if="hit.policy_clause">{{ hit.policy_clause }}</code>
                  <span v-if="hit.policy_text">{{ hit.policy_text }}</span>
                </template>
                <span
                  v-else
                  class="qrd-muted"
                >—</span>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- kb_snippet 独立区块：RAG 检索条款原文，与 policy_text 严格分区不混用 -->
        <div
          v-if="kbHits.length"
          class="qc-kb-section"
        >
          <h4>知识库片段（kb_snippet）</h4>
          <div
            v-for="(hit, i) in kbHits"
            :key="i"
            class="qc-kb-snippet"
            :data-rule-id="hit.rule_id ?? undefined"
          >
            <span class="qc-kb-ref">{{ hit.rule_id ?? hit.word ?? '—' }}</span>
            <p class="qc-kb-text">
              {{ hit.kb_snippet }}
            </p>
          </div>
        </div>
      </section>

      <!-- 未审规则：文案固定「本通未审/未适用」，不得称已合规 -->
      <section
        v-if="detail.skipped_rules.length"
        class="qrd-section qc-skipped"
      >
        <h3>未审规则</h3>
        <p class="qrd-muted">
          以下规则本通未审/未适用：
        </p>
        <ul class="qc-skipped-list">
          <li
            v-for="ruleId in detail.skipped_rules"
            :key="ruleId"
          >
            <code>{{ ruleId }}</code>
            <span class="qc-skipped-tag">本通未审/未适用</span>
          </li>
        </ul>
      </section>

      <!-- 会话证据对照：说话方/轮次 + 证据原文命中词高亮 -->
      <section
        v-if="transcriptRows.length"
        class="qrd-section qc-transcript"
      >
        <h3>会话证据对照</h3>
        <ul class="qc-transcript-list">
          <li
            v-for="(row, i) in transcriptRows"
            :key="i"
            class="qc-transcript-item"
          >
            <span
              class="qc-speaker"
              :class="`qc-speaker--${row.hit.speaker}`"
            >{{ SPEAKER_LABELS[row.hit.speaker] ?? row.hit.speaker }}</span>
            <code class="qc-turn-id">{{ row.hit.turn_id ?? '—' }}</code>
            <span class="qc-evidence">
              <template
                v-for="(part, j) in row.parts"
                :key="j"
              ><mark
                v-if="part.hit"
                class="qc-evidence-mark"
              >{{ part.text }}</mark><template v-else>{{ part.text }}</template></template>
            </span>
          </li>
        </ul>
      </section>

      <!-- trace 时间线：仅 langsmith_url 存在时显示外链 -->
      <section class="qrd-section">
        <h3>链路 trace</h3>
        <p
          v-if="trace?.mcp_call_qc === 'not_called'"
          class="qrd-muted"
        >
          {{ trace.mcp_call_qc_note }}
        </p>
        <div
          v-if="traceLoading"
          class="qrd-skeleton"
        >
          trace 加载中…
        </div>
        <p
          v-else-if="traceError"
          class="qrd-muted"
        >
          trace 加载失败：{{ traceError }}
        </p>
        <TraceTimeline
          v-else-if="trace"
          :events="trace.events"
          :langsmith-url="trace.langsmith_url"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.qc-record-detail { padding: 16px 20px; display: flex; flex-direction: column; gap: 20px; }
.qrd-header { display: flex; align-items: center; gap: 12px; }
.qrd-title { margin: 0; font-size: 18px; color: #303133; }
.qrd-title code { font-size: 13px; color: #606266; }
.qrd-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qrd-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.qrd-skeleton { padding: 32px 0; text-align: center; color: #909399; font-size: 14px; }
.qrd-empty { padding: 48px 0; text-align: center; color: #909399; display: flex; flex-direction: column; gap: 12px; align-items: center; }
.qrd-error { display: flex; align-items: center; gap: 12px; padding: 24px 0; color: #f56c6c; font-size: 14px; }
.qrd-muted { color: #909399; font-size: 13px; margin: 0; }

.qrd-meta { border: 1px solid #ebeef5; border-radius: 6px; background: #fafbfc; padding: 12px 16px; }
.qrd-kv { margin: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px 24px; }
.qrd-kv-item { display: flex; flex-direction: column; gap: 2px; }
.qrd-kv-item dt { font-size: 12px; color: #909399; }
.qrd-kv-item dd { margin: 0; font-size: 14px; color: #303133; }
.qrd-deduction { font-weight: 600; color: #f56c6c; }

.qrd-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.qrd-tag--compliant { background: #f0f9eb; color: #67c23a; border: 1px solid #c2e7b0; }
.qrd-tag--violation { background: #fef0f0; color: #f56c6c; border: 1px solid #fbc4c4; }
.qrd-tag--pending_review { background: #fdf6ec; color: #e6a23c; border: 1px solid #f5dab1; }

.qrd-section h3 { margin: 0 0 10px; font-size: 15px; color: #303133; }
.qrd-section h4 { margin: 12px 0 6px; font-size: 13px; color: #606266; }

.qrd-table { width: 100%; border-collapse: collapse; border: 1px solid #ebeef5; background: white; }
.qrd-table th, .qrd-table td { padding: 10px 14px; border-bottom: 1px solid #ebeef5; text-align: left; font-size: 13px; }
.qrd-table th { background: #fafafa; font-weight: 600; color: #606266; }
.qc-hit-points { white-space: nowrap; }
.qc-hit-not-counted {
  margin-left: 6px; padding: 1px 6px; border-radius: 3px; font-size: 12px;
  background: #f4f4f5; color: #909399; border: 1px solid #e9e9eb;
}
.qc-hit-policy code { margin-right: 6px; }

.qc-kb-section { margin-top: 12px; border-top: 1px dashed #ebeef5; padding-top: 8px; }
.qc-kb-snippet {
  border: 1px solid #ebeef5; border-left: 3px solid #909399; border-radius: 4px;
  padding: 8px 12px; margin-bottom: 8px; background: #fafbfc;
}
.qc-kb-ref { font-size: 12px; color: #909399; }
.qc-kb-text { margin: 4px 0 0; font-size: 13px; color: #303133; white-space: pre-wrap; }

.qc-skipped-list { margin: 8px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.qc-skipped-tag {
  margin-left: 8px; padding: 1px 6px; border-radius: 3px; font-size: 12px;
  background: #fdf6ec; color: #e6a23c; border: 1px solid #f5dab1;
}

.qc-transcript-list { margin: 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
.qc-transcript-item { display: flex; align-items: baseline; gap: 8px; font-size: 13px; }
.qc-speaker { flex: none; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.qc-speaker--agent { background: #ecf5ff; color: #409eff; border: 1px solid #b3d8ff; }
.qc-speaker--user { background: #f4f4f5; color: #606266; border: 1px solid #e9e9eb; }
.qc-turn-id { flex: none; color: #909399; font-size: 12px; }
.qc-evidence { color: #303133; line-height: 1.6; }
.qc-evidence-mark { background: #ffe58f; padding: 0 2px; border-radius: 2px; }
</style>
