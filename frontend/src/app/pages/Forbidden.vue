<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/app/stores/auth'
import { firstAccessibleRoute } from '@/app/router/guards'

// 组件名多单词，满足 vue/multi-word-component-names 规则
defineOptions({ name: 'ForbiddenPage' })

// 越权兜底页（独立于 AdminLayout，SSOT §11.1）。
// 提供「返回首页」（按角色落点）与「重新登录」两个出口，不发任何数据请求。
const router = useRouter()
const auth = useAuthStore()

function goHome(): void {
  router.push(firstAccessibleRoute(auth.role))
}

function goLogin(): void {
  router.push('/login')
}
</script>

<template>
  <div class="forbidden-page">
    <el-result
      icon="warning"
      title="403"
      sub-title="抱歉，您没有访问该页面的权限"
    >
      <template #extra>
        <el-button
          type="primary"
          @click="goHome"
        >
          返回首页
        </el-button>
        <el-button @click="goLogin">
          重新登录
        </el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.forbidden-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}
</style>
