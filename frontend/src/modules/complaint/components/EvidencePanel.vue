<script setup lang="ts">
/**
 * 证据包面板（tasks 3.2）。
 * - props.evidence: detail.ordered_evidence（后端返回的证据条目列表）
 * - props.refs: detail.evidence_refs（RAG 源 ID 列表，用于回溯）
 */
import { computed } from 'vue'

const props = defineProps<{
  evidence: Record<string, unknown>[]
  refs: string[]
}>()

const evidenceCount = computed(() => props.evidence?.length ?? 0)
const refCount = computed(() => props.refs?.length ?? 0)
</script>

<template>
  <div class="evidence-panel">
    <el-descriptions :column="2" border size="small">
      <el-descriptions-item label="证据条目数">{{ evidenceCount }}</el-descriptions-item>
      <el-descriptions-item label="RAG 源数">{{ refCount }}</el-descriptions-item>
    </el-descriptions>

    <!-- 证据条目（后端结构未在 SSOT 逐字定义，按 Record 直接展示） -->
    <div v-if="evidenceCount" class="evidence-list">
      <pre v-for="(item, i) in evidence" :key="i" class="evidence-item">{{ JSON.stringify(item, null, 2) }}</pre>
    </div>
    <el-empty v-else description="暂无证据" />

    <!-- RAG 源 ID 回溯 -->
    <section v-if="refCount" class="ref-section">
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
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  overflow-x: auto;
}
.ref-section h4 { margin: 0 0 8px; font-size: 14px; }
</style>
