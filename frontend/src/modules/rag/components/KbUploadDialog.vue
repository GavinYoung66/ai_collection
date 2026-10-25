<script setup lang="ts">
/**
 * KbUploadDialog：上传对话框（f2-rag-console 批次④ / tasks 4.5，design D4）。
 *
 * - 纯展示组件：props 进 emit 出，不碰 api；上传请求由容器（KbDocuments）发起，
 *   B0001（重名/参数）经 serverError prop 在文件行内透出，不弹全局 toast
 *   （spec「重名上传被拒」）；前端不做重名预检（后端 B0001 防重，硬约束）；
 * - 扩展名白名单 14 种本地校验（gradio/app.py:45-48），白名单外本地拦截并提示
 *   支持格式清单，不发起请求（spec「各格式预览分档正确」exe 分支）；
 * - 本地预览四档分档，零新运行时依赖（D4 / spec「上传前本地预览必须按格式分档」）：
 *   txt/md → FileReader 文本预览（超 PREVIEW_CHARS 截断，formatters.py:37）；
 *   png/jpg/jpeg → object URL 图片；
 *   pdf → object URL iframe（超 8MB 不预览给提示，formatters.py:40 PDF_PREVIEW_MAX）；
 *   xlsx/docx/pptx/mp3/wav/m4a/amr/wma → 「该格式无内嵌预览」提示（formatters.py:43）；
 * - 换文件 / 关闭对话框 / 组件卸载时 revokeObjectURL 释放。
 */
import { computed, onScopeDispose, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { RagCategory } from '../types'
import { KB_SUMMARY_MAX_LEN, RAG_CATEGORY_LABELS } from '../types'

const props = defineProps<{
  /** 对话框显隐（v-model） */
  modelValue: boolean
  /** 提交中：确认按钮 loading + 防重复提交 */
  submitting: boolean
  /** B0001 服务端错误：文件行内透出（v-model:server-error） */
  serverError: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'update:serverError', v: string): void
  /** 提交意图：payload 已组装好，上传请求由容器负责 */
  (e: 'submit', payload: { file: File; category: RagCategory; summary: string }): void
}>()

/** el-dialog v-model 桥接（props 进 emit 出） */
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// ── 扩展名白名单与预览分档常量（来源：gradio/app.py:45-48、formatters.py:37/40-44）──
/** 14 种允许格式（app.py:45-48 _ALLOWED_EXTENSIONS） */
const ALLOWED_EXTENSIONS = [
  'txt', 'md', 'pdf', 'docx', 'xlsx', 'pptx',
  'png', 'jpg', 'jpeg', 'mp3', 'wav', 'm4a', 'amr', 'wma',
] as const
const SUPPORT_HINT = ALLOWED_EXTENSIONS.join(' / ')
const ACCEPT_ATTR = ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',')
/** 文本预览档（formatters.py:42 TEXT_EXTS） */
const TEXT_EXTS = new Set(['txt', 'md'])
/** 图片预览档（formatters.py:41 IMAGE_EXTS） */
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg'])
/** 文本预览截断长度（formatters.py:37 PREVIEW_CHARS） */
const TEXT_PREVIEW_CHARS = 800
/** PDF 内嵌预览体积上限（formatters.py:40 PDF_PREVIEW_MAX，防内存尖峰） */
const PDF_PREVIEW_MAX = 8 * 1024 * 1024

/** 分类选项：中文标签 + code 值（对齐 formatters.py:22-27，前端不发明分类） */
const CATEGORY_OPTIONS: { value: RagCategory; label: string }[] = [
  { value: 'regulation', label: RAG_CATEGORY_LABELS.regulation },
  { value: 'script', label: RAG_CATEGORY_LABELS.script },
  { value: 'case', label: RAG_CATEGORY_LABELS.case },
  { value: 'other', label: RAG_CATEGORY_LABELS.other },
]

// ── 表单（分类必选；摘要可选 ≤200 字 rules 行内标红不发请求）──
const formRef = ref<FormInstance>()
const form = reactive({ category: '' as RagCategory | '', summary: '' })
const formRules: FormRules = {
  category: [{ required: true, message: '请选择分类', trigger: 'change' }],
  summary: [
    {
      max: KB_SUMMARY_MAX_LEN,
      message: `摘要不能超过 ${KB_SUMMARY_MAX_LEN} 字`,
      trigger: ['blur', 'change'],
    },
  ],
}

// ── 文件选择与本地校验 ──
const fileInputRef = ref<HTMLInputElement>()
const selectedFile = ref<File | null>(null)
/** 本地校验错误（白名单拦截/未选文件）：文件行内透出 */
const localError = ref('')

function extOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

// ── 本地预览（四档分档；object URL 统一经 revokePreviewUrl 释放）──
type PreviewKind = 'text' | 'image' | 'pdf' | 'hint' | null
const previewKind = ref<PreviewKind>(null)
const previewText = ref('')
const previewTruncated = ref(false)
const previewUrl = ref('')
const previewHint = ref('')
/** 当前持有的 object URL（模块内单文件选择，至多一个存活） */
let objectUrl: string | null = null

function revokePreviewUrl(): void {
  if (objectUrl !== null) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = null
  }
  previewUrl.value = ''
}

function clearPreview(): void {
  revokePreviewUrl()
  previewKind.value = null
  previewText.value = ''
  previewTruncated.value = false
  previewHint.value = ''
}

/** 按格式分档构建预览（D4；预览仅为上传前辅助确认，失败不阻塞提交） */
function buildPreview(file: File, ext: string): void {
  clearPreview()
  if (TEXT_EXTS.has(ext)) {
    // txt/md → FileReader 文本预览，超长截断
    const reader = new FileReader()
    reader.onload = () => {
      const text = typeof reader.result === 'string' ? reader.result : ''
      previewText.value = text.slice(0, TEXT_PREVIEW_CHARS)
      previewTruncated.value = text.length > TEXT_PREVIEW_CHARS
      previewKind.value = 'text'
    }
    reader.readAsText(file)
    return
  }
  if (IMAGE_EXTS.has(ext)) {
    // png/jpg/jpeg → object URL 图片
    objectUrl = URL.createObjectURL(file)
    previewUrl.value = objectUrl
    previewKind.value = 'image'
    return
  }
  if (ext === 'pdf') {
    // pdf → object URL iframe；超 8MB 不预览给提示
    if (file.size > PDF_PREVIEW_MAX) {
      previewHint.value = 'PDF 超过 8MB，不提供内嵌预览，可正常提交入库'
      previewKind.value = 'hint'
      return
    }
    objectUrl = URL.createObjectURL(file)
    previewUrl.value = objectUrl
    previewKind.value = 'pdf'
    return
  }
  // xlsx/docx/pptx/音频 → 无内嵌预览提示（formatters.py:43）
  previewHint.value = '该格式无内嵌预览，可正常提交入库'
  previewKind.value = 'hint'
}

function onFileChange(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  localError.value = ''
  emit('update:serverError', '')
  if (!file) {
    selectedFile.value = null
    clearPreview()
    return
  }
  const ext = extOf(file.name)
  // 白名单外本地拦截：不发起请求，提示支持格式清单（spec「各格式预览分档正确」）
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(ext)) {
    selectedFile.value = null
    clearPreview()
    input.value = '' // 清空选择，允许重选同名文件再次触发 change
    localError.value = `不支持 .${ext || '未知'} 格式，支持格式：${SUPPORT_HINT}`
    return
  }
  selectedFile.value = file
  buildPreview(file, ext)
}

// ── 提交（请求由容器负责；本地校验不过不发 emit）──
async function onSubmit(): Promise<void> {
  if (props.submitting) return
  localError.value = ''
  if (!selectedFile.value) {
    localError.value = '请先选择要上传的文件'
    return
  }
  // 分类必选 / 摘要 ≤200 字：rules 行内标红，请求不发出
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  emit('submit', {
    file: selectedFile.value,
    category: form.category as RagCategory,
    summary: form.summary.trim(),
  })
}

// ── 复位：每次打开回到初始态（关闭动画结束后触发，避免闪动）──
function resetState(): void {
  selectedFile.value = null
  localError.value = ''
  emit('update:serverError', '')
  form.category = ''
  form.summary = ''
  formRef.value?.clearValidate()
  if (fileInputRef.value) fileInputRef.value.value = ''
  clearPreview()
}

// 卸载兜底：object URL 必须释放（关闭对话框由 @closed → resetState 覆盖）
onScopeDispose(() => {
  revokePreviewUrl()
})
</script>

<template>
  <el-dialog
    v-model="visible"
    title="上传文档"
    width="560px"
    :close-on-click-modal="false"
    @closed="resetState"
  >
    <el-form
      ref="formRef"
      :model="form"
      :rules="formRules"
      label-width="72px"
    >
      <!-- 文件选择：白名单拦截与 B0001 均在此行内透出 -->
      <el-form-item
        label="文件"
        :error="localError || serverError || undefined"
      >
        <div class="kbu-file">
          <input
            ref="fileInputRef"
            type="file"
            :accept="ACCEPT_ATTR"
            aria-label="选择文件"
            @change="onFileChange"
          >
          <p class="kbu-hint">
            支持格式：{{ SUPPORT_HINT }}
          </p>
        </div>
      </el-form-item>

      <!-- 分类（必选，中文标签 + code 值） -->
      <el-form-item
        label="分类"
        prop="category"
      >
        <el-select
          v-model="form.category"
          placeholder="请选择分类"
          class="kbu-category"
          @change="emit('update:serverError', '')"
        >
          <el-option
            v-for="opt in CATEGORY_OPTIONS"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>

      <!-- 摘要（可选，≤200 字） -->
      <el-form-item
        label="摘要"
        prop="summary"
      >
        <el-input
          v-model="form.summary"
          type="textarea"
          :rows="4"
          :placeholder="`可选，不超过 ${KB_SUMMARY_MAX_LEN} 字`"
          @input="emit('update:serverError', '')"
        />
        <p class="kbu-hint kbu-hint--right">
          {{ form.summary.length }}/{{ KB_SUMMARY_MAX_LEN }}
        </p>
      </el-form-item>
    </el-form>

    <!-- 本地预览区（四档分档） -->
    <div
      v-if="previewKind"
      class="kbu-preview"
    >
      <template v-if="previewKind === 'text'">
        <pre class="kbu-preview-text">{{ previewText }}</pre>
        <p
          v-if="previewTruncated"
          class="kbu-hint"
        >
          内容超长，仅预览前 {{ TEXT_PREVIEW_CHARS }} 字
        </p>
      </template>
      <img
        v-else-if="previewKind === 'image'"
        :src="previewUrl"
        class="kbu-preview-image"
        alt="本地预览"
      >
      <iframe
        v-else-if="previewKind === 'pdf'"
        :src="previewUrl"
        class="kbu-preview-pdf"
        title="PDF 本地预览"
      />
      <p
        v-else
        class="kbu-hint"
      >
        {{ previewHint }}
      </p>
    </div>

    <template #footer>
      <el-button @click="visible = false">
        取消
      </el-button>
      <el-button
        type="primary"
        :loading="submitting"
        @click="onSubmit"
      >
        确认上传
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.kbu-file { display: flex; flex-direction: column; gap: 4px; width: 100%; }
.kbu-hint { margin: 0; font-size: 12px; color: #909399; }
.kbu-hint--right { text-align: right; }
.kbu-category { width: 100%; }

.kbu-preview {
  margin-top: 4px; padding: 8px 12px; border: 1px solid #ebeef5; border-radius: 4px;
  background: #fafbfc;
}
.kbu-preview-text {
  margin: 0; max-height: 240px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  font-size: 13px; color: #303133;
}
.kbu-preview-image { max-width: 100%; max-height: 280px; display: block; }
.kbu-preview-pdf { width: 100%; height: 320px; border: none; }
</style>
