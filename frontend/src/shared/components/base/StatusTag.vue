<script setup lang="ts">
// 状态标签组件：将状态值映射为 Element Plus tag 类型与展示文案。
// 仅依赖 props，不依赖 store 或 modules/；新增状态只需扩展 STATUS_MAP。
import { computed } from 'vue'

const props = defineProps<{
  status: string
}>()

// 状态 → el-tag type 与文案映射（F0 覆盖用户状态，后续业务状态按需扩展）
const STATUS_MAP: Record<string, { type: '' | 'success' | 'warning' | 'danger' | 'info'; label: string }> = {
  active: { type: 'success', label: '正常' },
  disabled: { type: 'info', label: '已停用' },
  locked: { type: 'warning', label: '已锁定' },
}

const tag = computed(() => STATUS_MAP[props.status] ?? { type: 'info', label: props.status ?? '-' })
</script>

<template>
  <el-tag
    :type="tag.type"
    size="small"
  >
    {{ tag.label }}
  </el-tag>
</template>
