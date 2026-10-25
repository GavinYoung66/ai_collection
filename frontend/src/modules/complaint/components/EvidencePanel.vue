<script setup lang="ts">
/**
 * 证据包面板（tasks 3.2）。
 * - props.evidence: detail.ordered_evidence（后端有序证据条目，EvidenceItem 结构化类型）
 * - props.refs: detail.evidence_refs（RAG 源 ID 列表，用于回溯）
 * 证据按结构化卡片渲染（category/name/summary/status/started_at），不再裸 JSON dump。
 */
import { computed } from 'vue'
import type { EvidenceItem } from '../types'

const props = defineProps<{
  evidence: EvidenceItem[]
  refs: string[]
}>()

const evidenceCount = computed(() => props.evidence?.length ?? 0)
const refCount = computed(() => props.refs?.length ?? 0)

/** 证据类别 → 展示标签（后端 category 为 MCP/RAG/Agent/LLM/其他） */
const CATEGORY_LABELS: Record<string, string> = {
  MCP: '工具调用',
  RAG: '知识库',
  Agent: 'Agent',
  LLM: 'LLM',
  其他: '其他',
}

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category
}
</script>

<template>
  <div class="evidence-panel">
    <el-descriptions
      :column="2"
      border
      size="small"
    >
      <el-descriptions-item label="证据条目数">
        {{ evidenceCount }}
      </el-descriptions-item>
      <el-descriptions-item label="RAG 源数">
        {{ refCount }}
      </el-descriptions-item>
    </el-descriptions>

    <!-- 证据条目：结构化卡片（summary 后端已脱敏） -->
    <div
      v-if="evidenceCount"
      class="evidence-list"
    >
      <div
        v-for="(item, i) in evidence"
        :key="`${item.ord}-${i}`"
        class="evidence-item"
      >
        <div class="evidence-head">
          <el-tag
            size="small"
            class="ev-cat"
          >
            {{ categoryLabel(item.category) }}
          </el-tag>
          <strong class="ev-name">{{ item.name || '—' }}</strong>
          <el-tag
            size="small"
            :type="item.status === 'success' ? 'success' : 'warning'"
            class="ev-status"
          >
            {{ item.status }}
          </el-tag>
          <span
            v-if="item.started_at"
            class="ev-time"
          >{{ item.started_at }}</span>
        </div>
        <p class="ev-summary">
          {{ item.summary || '—' }}
        </p>
      </div>
    </div>
    <el-empty
      v-else
      description="暂无证据"
    />

    <!-- RAG 源 ID 回溯 -->
    <section
      v-if="refCount"
      class="ref-section"
    >
      <h4>RAG 源 ID 回溯</h4>
      <el-tag
        v-for="ref in refs"
        :key="ref"
        size="small"
        style="margin-right: 4px; margin-bottom: 4px"
      >
        {{ ref }}
      </el-tag>
    </section>
  </div>
</template>

<style scoped>
.evidence-panel { display: flex; flex-direction: column; gap: 16px; }
.evidence-list { display: flex; flex-direction: column; gap: 8px; }
.evidence-item {
  background: #f5f7fa;
  padding: 10px 12px;
  border-radius: 4px;
  border-left: 3px solid #dcdfe6;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.evidence-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ev-cat { flex: none; }
.ev-name { font-size: 14px; color: #303133; }
.ev-status { flex: none; }
.ev-time { color: #909399; font-size: 12px; margin-left: auto; }
.ev-summary { margin: 0; color: #606266; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
.ref-section h4 { margin: 0 0 8px; font-size: 14px; }
</style>
