<script setup lang="ts">
/**
 * KbSearch：检索测试台（f2-rag-console 批次⑤ / tasks 5.1-5.2）。
 *
 * 规格（spec「检索测试台必须固定 answer=false 并展示召回明细」）：
 * - 参数面板（查询条件组件本地 ref，不进 store，design D5）：query 多行 1~500 字
 *   （rules 行内标红不发请求）、top_k el-slider 1~20 默认 5、分类含「全部」
 *   （空值不下发 category 字段）、rewrite el-switch 默认关（models/search.py:21-35）；
 * - 请求体禁带 answer 键：由 api 层显式逐字段组装保证（api.ts searchKb），
 *   类型层 RagSearchRequest 亦无 answer 字段，视图不碰该语义；
 * - 结果区：卡片列表展示 content/title/chunk_index/score/rerank_score
 *   （KbSearchResultCard 展示组件，props 进）；score 只展示原始数值不二次计算；
 *   degraded=true 时结果区顶部 el-alert 警示徽章展示 message 原文；
 *   响应 trace_id 用 TraceIdText 可复制；空结果按 results.length===0 判定
 *   空态「未找到相关内容」（§15.1 A3004 注，后端不抛该码）；
 * - 错误处理走 §15.1 白名单：不新增映射；B0006 限流由拦截器附退避文案，
 *   视图不自动重试（仅手动重试按钮）；其余码默认分支 message 直出
 *   （拦截器已附兜底文案 + trace 后 8 位）；
 * - keepAlive：检索台为单次调试动作，无自动刷新（无 onActivated 拉取）；
 *   表单与结果由 keepAlive 自然保留；不启用 trace 上下文注入（design D6），
 *   仅展示响应 trace_id。
 */
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import TraceIdText from '@/shared/components/base/TraceIdText.vue'
import { searchKb } from '../api'
import type { RagCategory, RagSearchResponse } from '../types'
import { RAG_CATEGORY_LABELS } from '../types'
import KbSearchResultCard from '../components/KbSearchResultCard.vue'

defineOptions({ name: 'KbSearch' })

/** query 长度上限（models/search.py:22 max_length=500） */
const QUERY_MAX_LEN = 500

/** 分类选项（含「全部」= 空值，不下发 category 字段；标签对齐 formatters.py:22-27） */
const CATEGORY_OPTIONS: { value: RagCategory | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'regulation', label: RAG_CATEGORY_LABELS.regulation },
  { value: 'script', label: RAG_CATEGORY_LABELS.script },
  { value: 'case', label: RAG_CATEGORY_LABELS.case },
  { value: 'other', label: RAG_CATEGORY_LABELS.other },
]

// ── 参数面板（本地 ref/reactive，不进 store）──
const formRef = ref<FormInstance>()
const form = reactive({
  query: '',
  topK: 5, // models/search.py:24 默认 5
  category: '' as RagCategory | '',
  rewrite: false, // models/search.py:25 默认 false
})
const formRules: FormRules = {
  query: [
    // 1~500 字（models/search.py:22 min_length=1, max_length=500）；
    // whitespace:true 拦截纯空白输入，超长行内标红不发请求
    { required: true, whitespace: true, message: '请输入检索内容', trigger: ['blur', 'change'] },
    { max: QUERY_MAX_LEN, message: `检索内容不能超过 ${QUERY_MAX_LEN} 字`, trigger: ['blur', 'change'] },
  ],
}

// ── 检索结果状态 ──
const searching = ref(false)
const response = ref<RagSearchResponse | null>(null)
const error = ref<Error | null>(null)
/** 是否已发起过检索（区分「初始态」与「空结果态」） */
const searched = ref(false)

/** 发起检索：rules 校验不过不发请求；请求体由 api 层组装（禁带 answer 键） */
async function onSearch(): Promise<void> {
  if (searching.value) return
  try {
    await formRef.value?.validate()
  } catch {
    // 校验失败：行内标红，请求不发出
    return
  }
  searching.value = true
  error.value = null
  response.value = null
  searched.value = true
  try {
    response.value = await searchKb({
      query: form.query,
      top_k: form.topK,
      rewrite: form.rewrite,
      ...(form.category ? { category: form.category } : {}),
    })
  } catch (e) {
    // §15.1 白名单：B0006 限流等由拦截器附文案 + trace 后 8 位，不自动重试；
    // 其余码默认分支 message 直出（含 trace），手动重试由用户点击发起
    error.value = e instanceof Error ? e : new Error(String(e))
  } finally {
    searching.value = false
  }
}

/** 错误态手动重试（重新走校验与请求，非自动重试） */
function onRetry(): void {
  void onSearch()
}
</script>

<template>
  <div class="kb-search">
    <header class="kbs-header">
      <h2 class="kbs-title">
        检索测试台
      </h2>
    </header>

    <!-- 参数面板（query 1~500 字 / top_k 1~20 默认 5 / 分类含全部 / rewrite 默认关） -->
    <el-form
      ref="formRef"
      :model="form"
      :rules="formRules"
      class="kbs-panel"
      label-width="90px"
      @submit.prevent
    >
      <el-form-item
        label="检索内容"
        prop="query"
      >
        <el-input
          v-model="form.query"
          type="textarea"
          :rows="3"
          :placeholder="`请输入检索 query（1~${QUERY_MAX_LEN} 字）`"
        />
        <p class="kbs-hint kbs-hint--right">
          {{ form.query.length }}/{{ QUERY_MAX_LEN }}
        </p>
      </el-form-item>

      <el-form-item label="top_k">
        <div class="kbs-slider">
          <el-slider
            v-model="form.topK"
            :min="1"
            :max="20"
            :step="1"
            class="kbs-slider-bar"
          />
          <span class="kbs-slider-value">{{ form.topK }}</span>
        </div>
      </el-form-item>

      <el-form-item label="分类">
        <el-select
          v-model="form.category"
          class="kbs-category"
          aria-label="分类"
        >
          <el-option
            v-for="opt in CATEGORY_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="Query 改写">
        <el-switch v-model="form.rewrite" />
      </el-form-item>

      <el-form-item>
        <el-button
          type="primary"
          :loading="searching"
          @click="onSearch"
        >
          {{ searching ? '检索中…' : '发起检索' }}
        </el-button>
      </el-form-item>
    </el-form>

    <!-- 结果区 -->
    <section class="kbs-result">
      <!-- 加载态 -->
      <div
        v-if="searching"
        class="kbs-state"
      >
        检索中…
      </div>

      <!-- 错误态：message 直出（拦截器已附 trace 后 8 位），手动重试 -->
      <div
        v-else-if="error"
        class="kbs-state kbs-state--error"
        role="alert"
      >
        <span>检索失败：{{ error.message }}</span>
        <button
          type="button"
          class="kbs-btn"
          @click="onRetry"
        >
          重试
        </button>
      </div>

      <template v-else-if="response">
        <!-- 兜底降级警示徽章：degraded=true 时展示 message 原文 -->
        <el-alert
          v-if="response.degraded"
          type="warning"
          :title="response.message ?? '检索已降级'"
          :closable="false"
          show-icon
          class="kbs-degraded"
        />

        <!-- 响应 trace_id（可复制；D6：不注入 trace 上下文，仅展示响应值） -->
        <div class="kbs-trace">
          响应 trace_id：
          <TraceIdText :trace-id="response.trace_id" />
        </div>

        <!-- 空结果空态（results.length===0 判定，§15.1 A3004 注） -->
        <div
          v-if="response.results.length === 0"
          class="kbs-state"
        >
          未找到相关内容
        </div>

        <!-- 召回明细卡片列表 -->
        <div
          v-else
          class="kbs-cards"
        >
          <KbSearchResultCard
            v-for="(item, idx) in response.results"
            :key="`${item.doc_id}-${item.chunk_index}-${idx}`"
            :item="item"
          />
        </div>
      </template>

      <!-- 初始态（尚未发起检索） -->
      <div
        v-else-if="!searched"
        class="kbs-state"
      >
        填写检索参数后点击「发起检索」
      </div>
    </section>
  </div>
</template>

<style scoped>
.kb-search { padding: 16px 20px; }
.kbs-header { margin-bottom: 16px; }
.kbs-title { margin: 0; font-size: 18px; color: #303133; }
.kbs-panel {
  max-width: 720px; padding: 16px 16px 0; border: 1px solid #ebeef5;
  border-radius: 4px; background: #fff;
}
.kbs-hint { margin: 4px 0 0; font-size: 12px; color: #909399; }
.kbs-hint--right { text-align: right; width: 100%; }
.kbs-slider { display: flex; align-items: center; gap: 12px; width: 100%; }
.kbs-slider-bar { flex: 1; }
.kbs-slider-value { min-width: 24px; text-align: right; font-family: monospace; font-size: 13px; }
.kbs-category { width: 200px; }

.kbs-result { margin-top: 20px; max-width: 960px; }
.kbs-state {
  padding: 48px 0; text-align: center; color: #909399; font-size: 14px;
}
.kbs-state--error {
  display: flex; align-items: center; justify-content: center; gap: 12px;
  color: #f56c6c;
}
.kbs-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.kbs-degraded { margin-bottom: 12px; }
.kbs-trace {
  display: flex; align-items: center; gap: 4px;
  margin-bottom: 12px; font-size: 12px; color: #909399;
}
.kbs-cards { display: flex; flex-direction: column; gap: 12px; }
</style>
