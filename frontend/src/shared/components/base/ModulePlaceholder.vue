<script setup lang="ts">
import { Tools } from '@element-plus/icons-vue'

// 模块占位组件：F0 期所有业务模块统一渲染「建设中」，禁止发起任何后端请求。
// 仅依赖 props，不依赖 store 或 modules/。
defineProps<{
  title: string
}>()
</script>

<template>
  <div class="module-placeholder">
    <el-icon
      :size="48"
      color="#c0c4cc"
    >
      <Tools />
    </el-icon>
    <h2 class="title">
      {{ title }}
    </h2>
    <p class="desc">
      建设中
    </p>
  </div>
</template>

<style scoped>
.module-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  gap: 12px;
  color: #909399;
}
.module-placeholder .title {
  margin: 0;
  font-size: 20px;
  color: #303133;
}
.module-placeholder .desc {
  margin: 0;
  font-size: 14px;
}
</style>
