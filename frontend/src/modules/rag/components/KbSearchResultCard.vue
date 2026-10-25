<script setup lang="ts">
/**
 * KbSearchResultCard：检索召回明细卡片（f2-rag-console 批次⑤ / tasks 5.2）。
 *
 * - 纯展示组件：props 进，无 api 依赖；字段逐条透传 SourceItem
 *   （services/rag-service/app/models/search.py:44-72）；
 * - score/rerank_score 只展示后端返回的原始数值，前端不做任何二次计算
 *   （spec「检索测试台必须固定 answer=false 并展示召回明细」/ SSOT §17）。
 */
import type { RagSearchResultItem } from '../types'

defineProps<{
  /** 召回明细项（models/search.py:44-72 SourceItem） */
  item: RagSearchResultItem
}>()
</script>

<template>
  <el-card
    class="kbc-card"
    shadow="never"
  >
    <div class="kbc-head">
      <span
        class="kbc-title"
        :title="item.title"
      >{{ item.title }}</span>
      <span class="kbc-chunk">chunk #{{ item.chunk_index }}</span>
    </div>
    <!-- 分数只展示原始值，不二次计算 -->
    <div class="kbc-scores">
      <span class="kbc-score">score：<code>{{ item.score }}</code></span>
      <span class="kbc-score">rerank_score：<code>{{ item.rerank_score }}</code></span>
    </div>
    <p class="kbc-content">
      {{ item.content }}
    </p>
  </el-card>
</template>

<style scoped>
.kbc-card { border: 1px solid #ebeef5; }
.kbc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.kbc-title {
  font-size: 14px; font-weight: 600; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kbc-chunk { flex-shrink: 0; font-size: 12px; color: #909399; font-family: monospace; }
.kbc-scores { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: #909399; }
.kbc-score code { background: #f4f4f5; padding: 1px 6px; border-radius: 3px; font-family: monospace; }
.kbc-content {
  margin: 10px 0 0; font-size: 13px; color: #606266; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
</style>
