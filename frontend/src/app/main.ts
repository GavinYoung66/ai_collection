import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'
import { useAuthStore, setupAuthHandlers } from './stores/auth'
import './styles/index.css'

// 创建应用实例
const app = createApp(App)

// 状态管理（Pinia）—— 必须先于 useAuthStore() 安装
const pinia = createPinia()
app.use(pinia)

// 路由
app.use(router)

// UI 组件库
app.use(ElementPlus)

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
