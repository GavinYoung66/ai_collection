<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'

// traceId 展示组件：界面仅显示后 8 位，点击复制完整值（用于错误提示与审计）。
// 仅依赖 props，不依赖 store 或 modules/。
const props = defineProps<{
  traceId: string
}>()

// 展示后 8 位；不足 8 位则原样展示
const shortId = computed(() => {
  const id = props.traceId ?? ''
  return id.length > 8 ? id.slice(-8) : id
})

// 点击复制完整 traceId 到剪贴板
async function copyTraceId(): Promise<void> {
  if (!props.traceId) return
  try {
    await window.navigator.clipboard.writeText(props.traceId)
    ElMessage.success('traceId 已复制')
  } catch {
    // 剪贴板权限被拒时降级提示
    ElMessage.warning('复制失败，请手动复制')
  }
}
</script>

<template>
  <span
    class="trace-id-text"
    title="点击复制完整 traceId"
    @click="copyTraceId"
  >
    <el-icon :size="12">
      <CopyDocument />
    </el-icon>
    <code>{{ shortId }}</code>
  </span>
</template>

<style scoped>
.trace-id-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: #909399;
  font-size: 12px;
}
.trace-id-text code {
  background: #f4f4f5;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: monospace;
}
</style>
