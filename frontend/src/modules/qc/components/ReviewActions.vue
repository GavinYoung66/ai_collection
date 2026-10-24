<script setup lang="ts">
/**
 * 人审操作组（f2-qc-console 批次4 完整落地；批次5 QcReview 页直接复用本组件）。
 *
 * 规格（spec「人工复核必须按后端枚举与扣分状态呈现」，design D3 按钮级 allowlist）：
 * - 零扣分（deduction_total=0 或缺省，fail-closed）→ 不渲染提交按钮，状态 DONE 即为完成；
 * - 「扣分正确」→ action=CONFIRM 直提（不改扣分）；
 *   「误报/改扣分」→ action=CORRECT 且 revised_deduction 必填（>=0 整数，缺值前端拦截不发请求）；
 * - 前端不暴露后端三值枚举中的驳回动作（spec 禁令）；REVIEWED 后全部按钮禁用；
 * - §15.1 白名单：CORRECT 表单收到 A2002（质检入参不合法，qc 的 422 不叠 B0001）→
 *   输入框行内标红 + 行内错误文案，不弹全局错误；其余码走默认分支 ElMessage 直出；
 * - 当前角色 ∉ {qc_reviewer, supervisor} → 置灰 + 说明「仅 qc_reviewer/supervisor 可复核」，
 *   不发起请求（角色经 useCurrentRole inject 桥取，模块不 import app store）；
 * - 提交中防连点；成功后 emit('submitted') 由宿主页面刷新。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { RequestError } from '@/shared/request'
import { submitQcReview } from '../api'
import type { QcReviewSubmitResponse, QcStatus } from '../types'
import { useCurrentRole } from '../composables/useCurrentRole'

const props = defineProps<{
  /** 复核路径参数（reviews/{qc_record_id}） */
  qcRecordId: string
  /** 当前机检/人审扣分合计；0 即零扣分 */
  deductionTotal: number
  qcStatus: QcStatus
}>()

const emit = defineEmits<{
  (e: 'submitted', res: QcReviewSubmitResponse): void
}>()

/** 按钮级 allowlist（后端 reviews 词表，design D3；admin 不在列） */
const REVIEW_ROLES = ['qc_reviewer', 'supervisor'] as const

const role = useCurrentRole()
const roleAllowed = computed(() => !!role.value && (REVIEW_ROLES as readonly string[]).includes(role.value))
const isReviewed = computed(() => props.qcStatus === 'REVIEWED')
// 零扣分 fail-closed：0 或缺省（undefined/null）都视为零扣分，不渲染提交按钮
const zeroDeduction = computed(() => !props.deductionTotal)

/** 提交中（防连点）、「误报/改扣分」输入态与 A2002 行内错误 */
const submitting = ref(false)
const correctMode = ref(false)
const revisedInput = ref('')
const formError = ref<string | null>(null)

/** 按钮统一禁用口径：越权 / 已人审 / 在途 */
const buttonsDisabled = computed(() => !roleAllowed.value || isReviewed.value || submitting.value)

/** 置灰原因说明（仅置灰时展示） */
const disabledReason = computed(() => {
  if (!roleAllowed.value) return '仅 qc_reviewer/supervisor 可复核，当前角色不可操作'
  if (isReviewed.value) return '本通已人审，复核按钮已锁定'
  return ''
})

/** 扣分正确 = CONFIRM 直提（spec：不改扣分） */
async function submitConfirm(): Promise<void> {
  if (buttonsDisabled.value) return
  submitting.value = true
  try {
    const res = await submitQcReview(props.qcRecordId, { action: 'CONFIRM' })
    ElMessage.success('复核已提交')
    emit('submitted', res)
  } catch (e) {
    ElMessage.error(`复核提交失败：${(e as Error).message}`)
  } finally {
    submitting.value = false
  }
}

/** 误报/改扣分 = CORRECT：revised_deduction 必填 >=0 整数，缺值/非法前端拦截不发请求；
 *  与旧演示页 app.js:717-721 对齐：改后扣分等于机检扣分时拦截并引导改值或用「扣分正确」；
 *  A2002 → 表单行内标红（§15.1，qc 的 422 不叠 B0001），其余码 ElMessage 直出 */
async function submitCorrect(): Promise<void> {
  if (buttonsDisabled.value) return
  const text = revisedInput.value.trim()
  if (!/^\d+$/.test(text)) {
    ElMessage.warning('请填写改后扣分（>=0 整数）')
    return
  }
  if (Number(text) === props.deductionTotal) {
    ElMessage.warning('扣分仍等于机检扣分。请改值后再提交，或点「扣分正确」')
    return
  }
  submitting.value = true
  formError.value = null
  try {
    const res = await submitQcReview(props.qcRecordId, {
      action: 'CORRECT',
      revised_deduction: Number(text),
    })
    ElMessage.success('复核已提交')
    correctMode.value = false
    revisedInput.value = ''
    emit('submitted', res)
  } catch (e) {
    const err = e as RequestError
    if (err.code === 'A2002') {
      // 白名单动作：表单行内标红，不弹全局错误
      formError.value = err.message
    } else {
      ElMessage.error(`复核提交失败：${err.message}`)
    }
  } finally {
    submitting.value = false
  }
}

/** 进入/退出改扣分模式（退出清空输入与行内错误） */
function toggleCorrectMode(): void {
  if (buttonsDisabled.value) return
  correctMode.value = !correctMode.value
  if (!correctMode.value) {
    revisedInput.value = ''
    formError.value = null
  }
}

/** 输入变化时清掉行内错误（用户正在修正） */
function onRevisedInput(): void {
  formError.value = null
}
</script>

<template>
  <div class="qc-review-actions">
    <!-- 零扣分：不渲染提交按钮（spec：状态 DONE 即为完成） -->
    <p
      v-if="zeroDeduction"
      class="qc-review-note qc-review-note--zero"
    >
      零扣分已完成，无需人审。
    </p>

    <template v-else>
      <div class="qc-review-row">
        <button
          type="button"
          class="qc-review-btn qc-review-btn--primary"
          :disabled="buttonsDisabled"
          @click="submitConfirm"
        >
          扣分正确
        </button>
        <button
          type="button"
          class="qc-review-btn"
          :disabled="buttonsDisabled"
          @click="toggleCorrectMode"
        >
          误报/改扣分
        </button>
      </div>

      <!-- 改扣分输入区：CORRECT 时 revised_deduction 必填（>=0 整数） -->
      <div
        v-if="correctMode"
        class="qc-review-correct"
      >
        <label class="qc-review-field">
          改后扣分（&gt;=0 整数）
          <input
            v-model="revisedInput"
            class="qc-review-input"
            :class="{ 'qc-review-input--error': !!formError }"
            type="text"
            inputmode="numeric"
            placeholder="整数扣分 >=0"
            :disabled="submitting"
            @input="onRevisedInput"
          >
        </label>
        <button
          type="button"
          class="qc-review-btn qc-review-btn--primary"
          :disabled="buttonsDisabled"
          @click="submitCorrect"
        >
          {{ submitting ? '提交中…' : '提交人审' }}
        </button>
      </div>

      <!-- A2002 行内错误（表单行内标红，不弹全局错误） -->
      <p
        v-if="formError"
        class="qc-review-field-error"
        role="alert"
      >
        {{ formError }}
      </p>

      <!-- 置灰原因说明（越权 / 已人审） -->
      <p
        v-if="disabledReason"
        class="qc-review-note"
        role="note"
      >
        {{ disabledReason }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.qc-review-actions { display: flex; flex-direction: column; gap: 8px; }
.qc-review-row { display: flex; gap: 8px; }
.qc-review-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qc-review-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.qc-review-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.qc-review-correct { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; }
.qc-review-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #606266; }
.qc-review-input {
  padding: 6px 8px; border: 1px solid #dcdfe6; border-radius: 4px; font-size: 13px; width: 160px;
}
.qc-review-input--error { border-color: #f56c6c; }
.qc-review-field-error { margin: 0; font-size: 12px; color: #f56c6c; }
.qc-review-note { margin: 0; font-size: 12px; color: #909399; }
.qc-review-note--zero { color: #67c23a; }
</style>
