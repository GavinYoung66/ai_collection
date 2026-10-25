<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/app/stores/auth'
import { firstAccessibleRoute } from '@/app/router/guards'

// 组件名多单词，满足 vue/multi-word-component-names 规则
defineOptions({ name: 'NotFoundPage' })

// 未知路径兜底页（SSOT §19.3）：独立于 AdminLayout，避免「有壳无内容」白页。
// 不发任何数据请求，只提供「返回首页」（按角色落点）与「返回上一页」两个出口。
const router = useRouter()
const auth = useAuthStore()

function goHome(): void {
  router.push(firstAccessibleRoute(auth.role))
}

function goBack(): void {
  // history 为空（直接打开深链）时回退会离开站点，此时退回首页
  if (window.history.length > 1) {
    router.back()
  } else {
    goHome()
  }
}
</script>

<template>
  <div class="not-found-page">
    <el-result
      icon="info"
      title="404"
      sub-title="抱歉，您访问的页面不存在或已被移除"
    >
      <template #extra>
        <el-button
          type="primary"
          @click="goHome"
        >
          返回首页
        </el-button>
        <el-button @click="goBack">
          返回上一页
        </el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
</style>
