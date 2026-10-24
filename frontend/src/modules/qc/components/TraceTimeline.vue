<script setup lang="ts">
/**
 * QC trace 事件时间线（f2-qc-console 批次4 / tasks 5.4，design D4）。
 *
 * 模块内自建、不下沉 shared：QC trace 事件形状为 category/name/status/summary +
 * langsmith_url，与投诉研判事件流语义不同，§12 规定 shared/base 组件无业务语义。
 * - events 按 started_at 升序渲染（status 值以后端为准：ok/error/skipped 等）
 * - 仅当 langsmithUrl 非空时渲染 LangSmith 外链（spec「trace 外链条件渲染」）
 */
import { computed } from 'vue'
import type { QcTraceEvent } from '../types'

const props = defineProps<{
  events: QcTraceEvent[]
  /** 后端返回的 LangSmith 外链；null/空串 时不渲染外链 */
  langsmithUrl?: string | null
}>()

// 按 started_at 升序
const sorted = computed(() =>
  [...props.events].sort(
    (a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime(),
  ),
)

/** status → 展示样式（后端状态词表不固定，前端只做颜色映射不改词） */
function statusClass(status: string): string {
  if (status === 'ok' || status === 'success') return 'qc-trace-status--ok'
  if (status === 'error') return 'qc-trace-status--error'
  return 'qc-trace-status--other'
}
</script>

<template>
  <div class="qc-trace-timeline">
    <p
      v-if="!events.length"
      class="qc-trace-empty"
    >
      暂无 trace 事件
    </p>
    <ol
      v-else
      class="qc-trace-list"
    >
      <li
        v-for="(evt, i) in sorted"
        :key="i"
        class="qc-trace-item"
      >
        <div class="qc-trace-head">
          <span class="qc-trace-cat">{{ evt.category }}</span>
          <strong class="qc-trace-name">{{ evt.name }}</strong>
          <span
            class="qc-trace-status"
            :class="statusClass(evt.status)"
          >{{ evt.status }}</span>
          <span class="qc-trace-meta">{{ evt.duration_ms }}ms · {{ evt.started_at }}</span>
        </div>
        <pre class="qc-trace-summary">{{ evt.summary }}</pre>
      </li>
    </ol>
    <!-- LangSmith 外链：仅响应含 langsmith_url 时渲染 -->
    <a
      v-if="langsmithUrl"
      class="qc-langsmith-link"
      :href="langsmithUrl"
      target="_blank"
      rel="noopener noreferrer"
    >在 LangSmith 打开</a>
  </div>
</template>

<style scoped>
.qc-trace-timeline { padding: 4px 0; }
.qc-trace-empty { color: #909399; font-size: 13px; }
.qc-trace-list { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 10px; }
.qc-trace-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.qc-trace-cat {
  padding: 1px 8px; border-radius: 10px; font-size: 12px;
  background: #f4f4f5; color: #606266; border: 1px solid #e9e9eb;
}
.qc-trace-name { font-size: 13px; color: #303133; }
.qc-trace-status { font-size: 12px; padding: 1px 6px; border-radius: 3px; }
.qc-trace-status--ok { background: #f0f9eb; color: #67c23a; }
.qc-trace-status--error { background: #fef0f0; color: #f56c6c; }
.qc-trace-status--other { background: #fdf6ec; color: #e6a23c; }
.qc-trace-meta { color: #909399; font-size: 12px; }
.qc-trace-summary {
  margin: 4px 0 0; font-size: 12px; color: #606266;
  white-space: pre-wrap; font-family: inherit;
}
.qc-langsmith-link {
  display: inline-block; margin-top: 10px; font-size: 13px; color: #409eff;
}
</style>
