import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'
import { useAuthStore, setupAuthHandlers } from './stores/auth'
import { http, registerErrorReporter } from '../shared/request'
import './styles/index.css'

// ── backend-security-completion REQ-5a：全局错误 → 审计端点 fire-and-forget ──
// 钩子闭包：捕获 window 级 + Vue 生命周期内 + axios 业务非零码三类错误
// 统一 POST 到后端 /api/v1/audit/frontend-error（_skipAuthInterceptor=true
// 避免拦截器覆盖 Authorization 头——此端点走已登录态，拦截器会自动注入 token）
function setupErrorAuditBridge(): void {
  const post = (payload: Record<string, unknown>) => {
    http
      .post<unknown, unknown>('/api/v1/audit/frontend-error', payload)
      .catch(() => { /* 审计失败静默吞：绝不阻塞 UI */ })
  }

  // axios 请求层：注册 reporter，401/B0002 等非零码自动遍历
  registerErrorReporter((err) => {
    post({
      error_type: err.code || 'RequestError',
      message: err.message,
      url: err.config?.url || location.pathname,
      line: undefined,
      col: undefined,
      user_agent: navigator.userAgent.slice(0, 300),
      trace_id: err.trace,
    })
  })

  // window 级 JS 错误（同步 + 事件处理）
  window.addEventListener('error', (evt: ErrorEvent) => {
    post({
      error_type: 'UnhandledError',
      message: evt.message || (evt.error?.message ?? ''),
      stack_trace: evt.error?.stack,
      url: evt.filename || location.href,
      line: evt.lineno || undefined,
      col: evt.colno || undefined,
      user_agent: navigator.userAgent.slice(0, 300),
    })
  })

  // Promise 未捕获拒绝
  window.addEventListener('unhandledrejection', (evt: PromiseRejectionEvent) => {
    const reason = evt.reason
    post({
      error_type: 'UnhandledRejection',
      message: reason instanceof Error ? reason.message : String(reason),
      stack_trace: reason instanceof Error ? reason.stack : undefined,
      url: location.href,
      user_agent: navigator.userAgent.slice(0, 300),
    })
  })
}
setupErrorAuditBridge()

// 创建应用实例
const app = createApp(App)

// 状态管理（Pinia）—— 必须先于 useAuthStore() 安装
const pinia = createPinia()
app.use(pinia)

// 路由
app.use(router)

// UI 组件库
app.use(ElementPlus)

// 全局异常兜底（SSOT §19.3）：渲染/生命周期内未捕获异常统一提示，避免白屏且无痕；
// 完整堆栈仍进 console，便于排查。
app.config.errorHandler = (err, _instance, info) => {
  console.error('[app:error]', info, err)
  ElMessage.error('页面出现异常，请刷新或返回重试')
}

// 注册请求层回调（401 清态跳转 / 429 退避提示）
setupAuthHandlers(router)

// 启动期：读 localStorage token，有则拉 /me 重建 user/role
useAuthStore().initFromStorage()

// —— 开发态 mock：仅在 DEV 下条件引入 mocks/ ——
// 该动态 import 在生产构建的死分支中会被 tree-shake，不会进入产物；
// 同时 depcruise 规则保证只有本文件能引用 mocks/。
if (import.meta.env.DEV) {
  import('@/mocks/browser').then(({ setupMocks }) => {
    setupMocks?.()
  })
}

app.mount('#app')
