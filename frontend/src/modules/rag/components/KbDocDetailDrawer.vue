<script setup lang="ts">
/**
 * KbDocDetailDrawer：文档详情抽屉（f2-rag-console 批次③ / tasks 4.2，design D3）。
 *
 * - 纯展示组件：props 进、emit 出，不碰 api；详情数据由容器（KbDocuments）获取后传入；
 * - 以 ElDrawer 呈现 GET /api/v1/kb/documents/{doc_id} 全字段（documents.py:149-190），
 *   含 ingest_stage/ingest_progress/ingest_error 与 assets 元数据表格（spec
 *   「文档详情与维护操作必须如实呈现后端语义」）；
 * - B0004 → 抽屉内空态 + 「返回列表」（§15.1 白名单动作，关闭抽屉即返回列表）；
 * - 其他错误（含 B0007/B0008/B0009 兜底文案）→ message 直出 + TraceIdText + 重试；
 * - 底栏操作（编辑摘要/重建索引/删除）仅 emit 意图，确认框与请求由容器负责；
 * - 按钮级权限（tasks 7.1，§5.0 F 后端词表 admin/supervisor）：canManage=false 时
 *   底栏写操作按钮置灰 + el-tooltip 悬浮说明「该操作需 admin/supervisor 权限」，
 *   置灰态不 emit（容器侧另有前置守卫兜底，双保险）。
 */
import { computed } from 'vue'
import StatusTag from '@/shared/components/base/StatusTag.vue'
import TraceIdText from '@/shared/components/base/TraceIdText.vue'
import type { RagDocumentDetailResponse } from '../types'
import { RAG_CATEGORY_LABELS } from '../types'
import { RAG_WRITE_FORBIDDEN_TIP } from '../composables/useCurrentRole'

const props = defineProps<{
  /** 抽屉显隐（v-model） */
  modelValue: boolean
  /** 详情数据；null = 未加载/加载失败 */
  detail: RagDocumentDetailResponse | null
  loading: boolean
  /** B0004：文档不存在（空态 + 返回列表） */
  notFound: boolean
  /** 非 B0004 错误：拦截器已附白名单文案 + trace 后 8 位 */
  errorMessage: string | null
  errorTrace: string | null
  /** 删除/重建进行中：底栏按钮禁用防重复提交 */
  actionPending: boolean
  /** 按钮级权限（tasks 7.1）：当前角色是否可执行写操作（后端词表 admin/supervisor） */
  canManage: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 非 B0004 错误态的重试（容器按当前 doc_id 重拉详情） */
  (e: 'retry'): void
  /** 底栏操作意图：打开摘要编辑 / 重建索引 / 删除（确认与请求在容器） */
  (e: 'edit-summary'): void
  (e: 'reindex'): void
  (e: 'delete'): void
}>()

/** el-drawer v-model 桥接（props 进 emit 出） */
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

/** B0004 空态「返回列表」：关闭抽屉即回到列表 */
function backToList(): void {
  emit('update:modelValue', false)
}

/** 底栏写操作守卫（tasks 7.1）：置灰态不 emit 意图（容器侧另有守卫兜底）。
 *  多签名 emit 不支持联合类型实参（TS 重载解析），故显式分支 */
function emitManageAction(e: 'edit-summary' | 'reindex' | 'delete'): void {
  if (!props.canManage) return
  if (e === 'edit-summary') emit('edit-summary')
  else if (e === 'reindex') emit('reindex')
  else emit('delete')
}
</script>

<template>
  <el-drawer
    v-model="visible"
    title="文档详情"
    size="640px"
    class="kb-doc-drawer"
  >
    <!-- 加载态 -->
    <div
      v-if="loading"
      class="kbd-skeleton"
    >
      加载中…
    </div>

    <!-- B0004：详情空态 + 返回列表（§15.1 白名单动作） -->
    <div
      v-else-if="notFound"
      class="kbd-empty"
    >
      <p>文档不存在或已被删除</p>
      <button
        type="button"
        class="kbd-btn kbd-btn--primary"
        @click="backToList"
      >
        返回列表
      </button>
    </div>

    <!-- 通用错误态：message 直出（含兜底文案与 trace 后 8 位）+ TraceIdText + 重试 -->
    <div
      v-else-if="errorMessage"
      class="kbd-error"
      role="alert"
    >
      <span>{{ errorMessage }}</span>
      <TraceIdText
        v-if="errorTrace"
        :trace-id="errorTrace"
      />
      <button
        type="button"
        class="kbd-btn"
        @click="emit('retry')"
      >
        重试
      </button>
    </div>

    <!-- 详情全字段（documents.py:167-190） -->
    <div
      v-else-if="detail"
      class="kbd-body"
    >
      <dl class="kbd-kv">
        <div class="kbd-kv-item kbd-kv-item--wide">
          <dt>标题</dt>
          <dd>{{ detail.title }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>分类</dt>
          <dd>{{ RAG_CATEGORY_LABELS[detail.category] }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>status</dt>
          <dd><StatusTag :status="detail.status" /></dd>
        </div>
        <div class="kbd-kv-item">
          <dt>stage</dt>
          <dd>{{ detail.ingest_stage ?? '—' }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>入库进度</dt>
          <dd>{{ detail.ingest_progress !== null ? `${detail.ingest_progress}%` : '—' }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>chunks</dt>
          <dd>{{ detail.chunks_count }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>版本</dt>
          <dd>{{ detail.version }}</dd>
        </div>
        <div class="kbd-kv-item kbd-kv-item--wide">
          <dt>doc_id</dt>
          <dd><code class="kbd-code">{{ detail.id }}</code></dd>
        </div>
        <div class="kbd-kv-item">
          <dt>创建时间</dt>
          <dd>{{ detail.create_time || '—' }}</dd>
        </div>
        <div class="kbd-kv-item">
          <dt>更新时间</dt>
          <dd>{{ detail.update_time || '—' }}</dd>
        </div>
        <div class="kbd-kv-item kbd-kv-item--wide">
          <dt>摘要</dt>
          <dd>{{ detail.summary ?? '—' }}</dd>
        </div>
      </dl>

      <!-- 入库失败原因（failed 时读 ingest_error 定位，README §G） -->
      <div
        v-if="detail.ingest_error"
        class="kbd-ingest-error"
        role="alert"
      >
        入库失败原因：{{ detail.ingest_error }}
      </div>

      <!-- PG 正文全文 -->
      <section class="kbd-section">
        <h3>正文</h3>
        <pre class="kbd-content">{{ detail.content }}</pre>
      </section>

      <!-- assets 元数据表格（documents.py:181-189；无资产时为空数组） -->
      <section class="kbd-section">
        <h3>资产元数据（{{ detail.assets.length }} 项）</h3>
        <p
          v-if="!detail.assets.length"
          class="kbd-muted"
        >
          无资产
        </p>
        <table
          v-else
          class="kbd-table"
        >
          <thead>
            <tr>
              <th>asset_type</th>
              <th>page</th>
              <th>storage_path</th>
              <th>mime_type</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(asset, i) in detail.assets"
              :key="i"
            >
              <td>{{ asset.asset_type }}</td>
              <td>{{ asset.page }}</td>
              <td><code class="kbd-code">{{ asset.storage_path }}</code></td>
              <td>{{ asset.mime_type ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 底栏操作：仅 detail 就绪时可操作；确认框与请求由容器负责。
         按钮级权限（tasks 7.1）：canManage=false 置灰 + 悬浮说明；
         disabled 按钮不触发 hover，故外层包 span 供 el-tooltip 监听 -->
    <template
      v-if="detail"
      #footer
    >
      <div class="kbd-footer">
        <el-tooltip
          :disabled="canManage"
          :content="RAG_WRITE_FORBIDDEN_TIP"
          placement="top"
        >
          <span class="kbd-tooltip-wrap">
            <el-button
              :disabled="actionPending || !canManage"
              @click="emitManageAction('edit-summary')"
            >
              编辑摘要
            </el-button>
          </span>
        </el-tooltip>
        <el-tooltip
          :disabled="canManage"
          :content="RAG_WRITE_FORBIDDEN_TIP"
          placement="top"
        >
          <span class="kbd-tooltip-wrap">
            <el-button
              type="warning"
              :disabled="actionPending || !canManage"
              :loading="actionPending"
              @click="emitManageAction('reindex')"
            >
              重建索引
            </el-button>
          </span>
        </el-tooltip>
        <el-tooltip
          :disabled="canManage"
          :content="RAG_WRITE_FORBIDDEN_TIP"
          placement="top"
        >
          <span class="kbd-tooltip-wrap">
            <el-button
              type="danger"
              :disabled="actionPending || !canManage"
              :loading="actionPending"
              @click="emitManageAction('delete')"
            >
              删除文档
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.kbd-skeleton { padding: 32px 0; text-align: center; color: #909399; font-size: 14px; }
.kbd-empty {
  padding: 48px 0; display: flex; flex-direction: column; gap: 12px;
  align-items: center; color: #909399;
}
.kbd-error {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 24px 0; color: #f56c6c; font-size: 14px;
}
.kbd-btn {
  padding: 6px 14px; border: 1px solid #dcdfe6; background: white; color: #606266;
  border-radius: 4px; cursor: pointer; font-size: 13px;
}
.kbd-btn--primary { background: #409eff; border-color: #409eff; color: white; }
.kbd-muted { color: #909399; font-size: 13px; margin: 0; }

.kbd-body { display: flex; flex-direction: column; gap: 16px; }
.kbd-kv { margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 24px; }
.kbd-kv-item { display: flex; flex-direction: column; gap: 2px; }
.kbd-kv-item--wide { grid-column: 1 / -1; }
.kbd-kv-item dt { font-size: 12px; color: #909399; }
.kbd-kv-item dd { margin: 0; font-size: 14px; color: #303133; word-break: break-all; }
.kbd-code { font-family: monospace; font-size: 12px; }

.kbd-ingest-error {
  padding: 8px 12px; border: 1px solid #fbc4c4; border-radius: 4px;
  background: #fef0f0; color: #f56c6c; font-size: 13px; word-break: break-all;
}

.kbd-section h3 { margin: 0 0 8px; font-size: 14px; color: #303133; }
.kbd-content {
  margin: 0; max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  padding: 8px 12px; border: 1px solid #ebeef5; border-radius: 4px;
  background: #fafbfc; font-size: 13px; color: #303133;
}

.kbd-table { width: 100%; border-collapse: collapse; border: 1px solid #ebeef5; background: white; }
.kbd-table th, .kbd-table td {
  padding: 8px 12px; border-bottom: 1px solid #ebeef5; text-align: left;
  font-size: 13px; word-break: break-all;
}
.kbd-table th { background: #fafafa; font-weight: 600; color: #606266; }

.kbd-footer { display: flex; justify-content: flex-end; gap: 8px; }
.kbd-tooltip-wrap { display: inline-block; }
</style>
