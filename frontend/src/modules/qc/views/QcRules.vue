<script setup lang="ts">
/**
 * QcRules：规则管理（f2-qc-console 批次5 / tasks 6.3）。
 *
 * 规格（spec「规则管理必须为只读展示加热应用」，design Non-Goals）：
 * - 生效规则只读展示 GET rules/active（rules.py:31；响应为版本级负载：
 *   version/status/effective_at/same_word_window_ms/penalty（severity→points）/
 *   deduction_policy（rule_id→静态制度条款），R01~R14 规则行由 deduction_policy 键透传导出，
 *   前端不自行造规则字段）；
 * - MUST NOT 提供规则编辑/新增/删除入口（后端无接口，MVP 只读）；
 * - 规则热应用 POST rules/apply MUST ElMessageBox 二次确认后才发起；
 *   成功展示 previous_version → version 并刷新；失败 message 直出（默认分支）；
 * - 无生效版本（A2009）等错误：错误态 + 重试（message 直出 + trace 尾缀已在拦截器附加）；
 * - keepAlive：onMounted 拉一次，onActivated 刷新（首个激活去重，同其他列表页）。
 */
import { computed, onActivated, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { AsyncState } from '@/shared'
import { applyQcRules, getQcRulesActive } from '../api'
import type { QcRulesActiveResponse } from '../types'

defineOptions({ name: 'QcRules' })

// ── 生效规则状态 ──
const active = ref<QcRulesActiveResponse | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)

// ── 热应用状态 ──
const applyVersion = ref('')
const applying = ref(false)

/** 规则行模型：deduction_policy 键即 rule_id（R01~R14 等），条款字段透传不臆造 */
interface RuleRow {
  rule_id: string
  clause_no: string
  clause_text: string
}

/** 生效规则行：按 rule_id 排序透传；非字符串条款字段显示占位，不做前端补全 */
const ruleRows = computed<RuleRow[]>(() => {
  const policy = active.value?.deduction_policy ?? {}
  return Object.keys(policy)
    .sort()
    .map((ruleId) => {
      const entry = policy[ruleId] as { clause_no?: unknown; clause_text?: unknown } | null
      return {
        rule_id: ruleId,
        clause_no: typeof entry?.clause_no === 'string' ? entry.clause_no : '—',
        clause_text: typeof entry?.clause_text === 'string' ? entry.clause_text : '—',
      }
    })
})

/** 扣分档位（penalty：severity → points，键序不保证故按字母序排序后展示） */
const penaltyRows = computed(() => {
  const penalty = active.value?.penalty ?? {}
  return Object.keys(penalty)
    .sort()
    .map((severity) => ({ severity, points: penalty[severity] }))
})

async function fetchActive(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    active.value = await getQcRulesActive()
  } catch (e) {
    active.value = null
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchActive()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchActive()
})

/** 规则热应用：二次确认后才发起 POST rules/apply；成功展示版本切换并刷新 */
async function onApply(): Promise<void> {
  if (applying.value) return
  const version = applyVersion.value.trim()
  if (!version) {
    ElMessage.warning('请填写目标规则版本号')
    return
  }
  try {
    // 二次确认（spec：确认后才发起请求）
    await ElMessageBox.confirm(
      `确认将生效规则热应用切换为版本「${version}」？切换立即生效。`,
      '规则热应用确认',
      { type: 'warning', confirmButtonText: '确认应用', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消：不发起请求
    return
  }
  applying.value = true
  try {
    const res = await applyQcRules({ version })
    ElMessage.success(
      `规则已热应用：${res.previous_version ?? '无'} → ${res.version}`,
    )
    applyVersion.value = ''
    await fetchActive()
  } catch (e) {
    ElMessage.error(`规则热应用失败：${(e as Error).message}`)
  } finally {
    applying.value = false
  }
}
</script>

<template>
  <div class="qc-rules">
    <header class="qru-header">
      <h2 class="qru-title">
        规则管理
      </h2>
      <button
        type="button"
        class="qru-btn"
        :disabled="loading"
        @click="fetchActive"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <!-- 三态统一由 shared AsyncState 承载（SSOT §19.7）；错误前缀保持原文案 -->
    <AsyncState
      :loading="loading"
      :error="error"
      error-prefix="规则加载失败："
      @retry="fetchActive"
    >
      <template #default>
        <template v-if="active">
          <!-- 版本信息（只读，响应字段透传） -->
          <section class="qru-section">
            <h3>生效版本</h3>
            <dl class="qru-kv">
              <div class="qru-kv-item">
                <dt>版本号</dt>
                <dd><code>{{ active.version }}</code></dd>
              </div>
              <div class="qru-kv-item">
                <dt>状态</dt>
                <dd>{{ active.status }}</dd>
              </div>
              <div class="qru-kv-item">
                <dt>生效时间</dt>
                <dd>{{ active.effective_at ?? '—' }}</dd>
              </div>
              <div class="qru-kv-item">
                <dt>同词窗口（ms）</dt>
                <dd>{{ active.same_word_window_ms ?? '—' }}</dd>
              </div>
            </dl>
          </section>

          <!-- 扣分档位（penalty：severity → points，透传） -->
          <section class="qru-section">
            <h3>扣分档位</h3>
            <table class="qru-table qru-penalty">
              <thead>
                <tr>
                  <th>严重度</th>
                  <th>扣分</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in penaltyRows"
                  :key="row.severity"
                >
                  <td><code>{{ row.severity }}</code></td>
                  <td>-{{ row.points }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 规则列表（只读：deduction_policy 键即 rule_id，R01~R14；无任何编辑入口） -->
          <section class="qru-section">
            <h3>生效规则（{{ ruleRows.length }} 条，只读）</h3>
            <table class="qru-table qru-rules">
              <thead>
                <tr>
                  <th style="width: 110px">
                    规则 ID
                  </th>
                  <th style="width: 160px">
                    条款编号
                  </th>
                  <th>条款内容</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in ruleRows"
                  :key="row.rule_id"
                  :data-rule-id="row.rule_id"
                >
                  <td><code>{{ row.rule_id }}</code></td>
                  <td>{{ row.clause_no }}</td>
                  <td>{{ row.clause_text }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 规则热应用：二次确认后发起 -->
          <section class="qru-section">
            <h3>规则热应用</h3>
            <div class="qru-apply">
              <input
                v-model="applyVersion"
                class="qru-input"
                type="text"
                placeholder="目标版本号（如 v1.1）"
                :disabled="applying"
                aria-label="目标版本号"
              >
              <button
                type="button"
                class="qru-btn qru-btn--primary"
                :disabled="applying || !applyVersion.trim()"
                @click="onApply"
              >
                {{ applying ? '应用中…' : '热应用' }}
              </button>
            </div>
            <p class="qru-muted">
              热应用将原子切换生效规则版本，需二次确认；当前版本：{{ active.version }}。
            </p>
          </section>
        </template>
      </template>
    </AsyncState>
  </div>
</template>

<style scoped>
.qc-rules { padding: 16px 20px; display: flex; flex-direction: column; gap: 20px; }
.qru-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.qru-title { margin: 0; font-size: 18px; color: #303133; }
.qru-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.qru-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.qru-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.qru-skeleton { padding: 48px 0; text-align: center; color: #909399; font-size: 14px; }
.qru-error {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  padding: 48px 0; color: #f56c6c; font-size: 14px;
}
.qru-section h3 { margin: 0 0 10px; font-size: 15px; color: #303133; }
.qru-kv { margin: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px 24px; }
.qru-kv-item { display: flex; flex-direction: column; gap: 2px; }
.qru-kv-item dt { font-size: 12px; color: #909399; }
.qru-kv-item dd { margin: 0; font-size: 14px; color: #303133; }
.qru-table { width: 100%; border-collapse: collapse; border: 1px solid #ebeef5; background: white; }
.qru-table th, .qru-table td { padding: 10px 14px; border-bottom: 1px solid #ebeef5; text-align: left; font-size: 13px; }
.qru-table th { background: #fafafa; font-weight: 600; color: #606266; }
.qru-apply { display: flex; gap: 8px; align-items: center; }
.qru-input {
  padding: 6px 8px; border: 1px solid #dcdfe6; border-radius: 4px; font-size: 13px; width: 200px;
}
.qru-muted { margin: 8px 0 0; font-size: 12px; color: #909399; }
</style>
