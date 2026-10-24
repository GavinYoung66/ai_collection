<script setup lang="ts">
/**
 * KbSensitive：敏感词词典（f2-rag-console 批次⑤ / tasks 6.1-6.2）。
 *
 * 规格（spec「敏感词页必须只读展示词典并提供刷新」）：
 * - 词典以等宽纯文本只读展示（GET current.dict，text/plain 非信封，走既有
 *   TEXT_WHITELIST，拦截器零改动：sensitive_words.py:28-46）；MUST NOT 出现
 *   列表/新增/编辑入口（敏感词无 CRUD 接口，SSOT §5.0 注 2），页面静态说明
 *   「敏感词无 CRUD 接口，仅词典查看与版本刷新」；
 * - 404（词典不存在/加载失败）由拦截器映射为空串（request.ts:126），
 *   前端据此展示空态「词典暂不可用」而非错误弹窗（sensitive_words.py:41-46）；
 * - 「刷新词库」ElMessageBox 二次确认后才发起 POST refresh（sensitive_words.py:49-63），
 *   成功展示返回的新 version（「词库版本：v{version}」）并重拉词典；
 *   B0006 限流由拦截器附退避文案，不自动重试；B0003 403 提示不重试；
 *   其余码默认分支 message 直出（§15.1 白名单零新增）；
 * - keepAlive：onMounted 拉一次 + onActivated 重拉词典（首激活去重，
 *   照 QcRules/QcRecordList 既有模式）；版本号由 keepAlive 自然保留；
 * - 按钮级权限（tasks 7.1，§5.0 F 后端词表 admin/supervisor）：「刷新词库」
 *   对越权角色（含 ai_trainer）置灰 + el-tooltip 悬浮说明
 *   「该操作需 admin/supervisor 权限」，入口前置守卫，置灰态不发起请求。
 */
import { onActivated, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSensitiveWordsDict, refreshSensitiveWords } from '../api'
import { RAG_WRITE_FORBIDDEN_TIP, useKbWriteAllowed } from '../composables/useCurrentRole'

defineOptions({ name: 'KbSensitive' })

// ── 按钮级权限（tasks 7.1）：词库刷新按后端词表 admin/supervisor（§5.0 F）──
const writeAllowed = useKbWriteAllowed()

// ── 词典状态 ──
const dict = ref('')
const loading = ref(false)
const error = ref<Error | null>(null)

/** 拉取生效词典；404 被拦截器映射为空串 → 空态「词典暂不可用」 */
async function fetchDict(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    dict.value = await getSensitiveWordsDict()
  } catch (e) {
    dict.value = ''
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    loading.value = false
  }
}

// keepAlive 语义：首个 activated 与 mounted 同序触发，标志位去重（同 QcRules 先例）
let skipNextActivated = false
onMounted(() => {
  skipNextActivated = true
  void fetchDict()
})
onActivated(() => {
  if (skipNextActivated) {
    skipNextActivated = false
    return
  }
  void fetchDict()
})

/** 复制词典全文到剪贴板（同 TraceIdText 复制语义；剪贴板被拒时降级提示） */
async function onCopy(): Promise<void> {
  if (!dict.value) return
  try {
    await window.navigator.clipboard.writeText(dict.value)
    ElMessage.success('词典内容已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

// ── 词库刷新（二次确认 → POST refresh → 展示新 version 并重拉词典）──
const refreshing = ref(false)
/** 最近一次刷新返回的版本号（sensitive_words.py:63；Redis INCR 整数） */
const version = ref<number | null>(null)

async function onRefresh(): Promise<void> {
  // 按钮级权限守卫：置灰态不发起请求（tasks 7.1）
  if (!writeAllowed.value) return
  if (refreshing.value) return
  try {
    // 二次确认（spec：刷新 MUST 二次确认后才发起）
    await ElMessageBox.confirm(
      '确认刷新敏感词词库？刷新后版本号自增，各实例将重新加载词典。',
      '刷新词库确认',
      { type: 'warning', confirmButtonText: '确认刷新', cancelButtonText: '取消' },
    )
  } catch {
    // 用户取消：不发起请求
    return
  }
  refreshing.value = true
  try {
    const res = await refreshSensitiveWords()
    version.value = res.version
    ElMessage.success(`词库已刷新，当前版本：v${res.version}`)
    // 词典内容可能随版本切换更新，重拉保持文本域为当前生效词典
    await fetchDict()
  } catch (e) {
    // §15.1 白名单：B0006 限流退避 / B0003 403 提示不重试，其余 message 直出
    // （拦截器已附兜底文案 + trace 后 8 位），不自动重试
    ElMessage.error((e as Error).message)
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="kb-sensitive">
    <header class="kbt-header">
      <h2 class="kbt-title">
        敏感词词典
      </h2>
      <div class="kbt-tools">
        <button
          type="button"
          class="kbt-btn"
          :disabled="loading || !dict"
          @click="onCopy"
        >
          复制
        </button>
        <!-- 刷新词库：按钮级权限（tasks 7.1）——越权角色置灰 + 悬浮说明，置灰不发起请求；
             disabled 按钮不触发 hover，故外层包 span 供 el-tooltip 监听 -->
        <el-tooltip
          :disabled="writeAllowed"
          :content="RAG_WRITE_FORBIDDEN_TIP"
          placement="top"
        >
          <span class="kbt-tooltip-wrap">
            <button
              type="button"
              class="kbt-btn kbt-btn--primary"
              :disabled="refreshing || !writeAllowed"
              @click="onRefresh"
            >
              {{ refreshing ? '刷新中…' : '刷新词库' }}
            </button>
          </span>
        </el-tooltip>
        <button
          type="button"
          class="kbt-btn"
          :disabled="loading"
          @click="fetchDict"
        >
          {{ loading ? '加载中…' : '重新加载' }}
        </button>
      </div>
    </header>

    <!-- 静态说明（SSOT §5.0 注 2：无 CRUD 接口） -->
    <p class="kbt-note">
      敏感词无 CRUD 接口，仅词典查看与版本刷新。
      <template v-if="version !== null">
        词库版本：v{{ version }}
      </template>
    </p>

    <!-- 加载态 -->
    <div
      v-if="loading"
      class="kbt-state"
    >
      加载中…
    </div>

    <!-- 错误态（非 404 的传输/信封错误）：message 直出 + 手动重试 -->
    <div
      v-else-if="error"
      class="kbt-state kbt-state--error"
      role="alert"
    >
      <span>词典加载失败：{{ error.message }}</span>
      <button
        type="button"
        class="kbt-btn"
        @click="fetchDict"
      >
        重试
      </button>
    </div>

    <!-- 空态：404（词典不存在/加载失败）→ 空态文案而非错误弹窗 -->
    <div
      v-else-if="!dict"
      class="kbt-state"
    >
      词典暂不可用
    </div>

    <!-- 词典只读展示：等宽纯文本 -->
    <pre
      v-else
      class="kbt-dict"
    >{{ dict }}</pre>
  </div>
</template>

<style scoped>
.kb-sensitive { padding: 16px 20px; }
.kbt-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; flex-wrap: wrap; gap: 8px;
}
.kbt-title { margin: 0; font-size: 18px; color: #303133; }
.kbt-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.kbt-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.kbt-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.kbt-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.kbt-tooltip-wrap { display: inline-block; }
.kbt-note { margin: 0 0 16px; font-size: 12px; color: #909399; }
.kbt-state { padding: 48px 0; text-align: center; color: #909399; font-size: 14px; }
.kbt-state--error {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  color: #f56c6c;
}
.kbt-dict {
  margin: 0; padding: 12px 16px; border: 1px solid #ebeef5; border-radius: 4px;
  background: #fafbfc; font-family: monospace; font-size: 13px; line-height: 1.6;
  color: #303133; white-space: pre-wrap; word-break: break-all;
  max-height: 560px; overflow: auto;
}
</style>
