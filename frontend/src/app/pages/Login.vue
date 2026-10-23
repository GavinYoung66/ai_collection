<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElForm, ElFormItem, ElInput, ElButton } from 'element-plus'
import { useAuthStore } from '@/app/stores/auth'
import { firstAccessibleRoute, isValidRedirect } from '@/app/router/guards'
import { formatTrace } from '@/shared/utils/error-map'
import type { RequestError } from '@/shared/request'

// 登录页（独立于 AdminLayout，SSOT §11.1 / D4）。
// 失败原地提示、不清空用户名；成功按 redirect 或角色落点跳转。
defineOptions({ name: 'LoginPage' })

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

// 开发便利：仅 DEV 模式预填种子账号（生产构建 import.meta.env.DEV=false，不含凭证）
// 去除时把两行初值改回 '' 即可
const username = ref(import.meta.env.DEV ? 'admin' : '')
const password = ref(import.meta.env.DEV ? 'Admin@2026' : '')
// 提交中状态：连点直接 return，保证 100ms 内只发 1 次请求
const loading = ref(false)
const errorMsg = ref('')

async function handleSubmit(): Promise<void> {
  // 提交中直接返回，防止重复提交
  if (loading.value) return
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    // 成功：redirect 合法则回跳，否则按角色落点
    const redirect = route.query.redirect as string | undefined
    if (redirect && isValidRedirect(redirect)) {
      router.push(redirect)
    } else {
      router.push(firstAccessibleRoute(auth.role))
    }
  } catch (err) {
    const e = err as RequestError
    // 按 code 区分：B0002 凭证错误 / B0003 账号停用；其余用 message
    // 提示附 trace 后 8 位（SSOT §15.1 / 5.3）
    if (e.code === 'B0002') {
      errorMsg.value = `用户名或密码错误（trace: ${formatTrace(e.trace)}）`
    } else if (e.code === 'B0003') {
      // 账号停用/锁定：不当作登录态失效，不清态、不跳转，原地提示
      errorMsg.value = `账号已停用或锁定（trace: ${formatTrace(e.trace)}）`
    } else {
      errorMsg.value = e.message || '登录失败'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2 class="login-title">
        智能催收 AI 平台
      </h2>
      <el-form
        label-position="top"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="用户名">
          <el-input
            v-model="username"
            placeholder="请输入用户名"
            :disabled="loading"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            show-password
            placeholder="请输入密码"
            :disabled="loading"
            @keyup.enter="handleSubmit"
          />
        </el-form-item>
        <div
          v-if="errorMsg"
          class="error-tip"
        >
          {{ errorMsg }}
        </div>
        <el-form-item>
          <el-button
            type="primary"
            class="login-btn"
            :loading="loading"
            @click="handleSubmit"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #f0f2f5;
}
.login-card {
  width: 380px;
}
.login-title {
  margin: 0 0 24px;
  text-align: center;
  font-size: 20px;
  color: #303133;
}
.error-tip {
  color: #f56c6c;
  font-size: 13px;
  margin-bottom: 12px;
  text-align: center;
}
.login-btn {
  width: 100%;
}
</style>
