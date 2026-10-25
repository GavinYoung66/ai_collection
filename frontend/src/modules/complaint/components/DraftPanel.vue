<script setup lang="ts">
/**
 * 草稿面板（tasks 3.2）。
 * - props.draft: detail.response_draft（草稿字符串或 null），只读展示
 */
defineProps<{
  draft: string | null
}>()
</script>

<template>
  <div class="draft-panel">
    <el-empty
      v-if="!draft"
      description="响应草稿尚未生成"
    />
    <pre
      v-else
      class="draft-content"
    >{{ draft }}</pre>
  </div>
</template>

<style scoped>
.draft-panel { padding: 8px 0; }
.draft-content {
  background: #f5f7fa;
  padding: 16px;
  border-radius: 4px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  margin: 0;
  color: #303133;
  border-left: 3px solid #409eff;
}
</style>
