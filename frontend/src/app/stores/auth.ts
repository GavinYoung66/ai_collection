import { defineStore } from 'pinia'
import type { Router } from 'vue-router'
import { ElMessage } from 'element-plus'
import http, {
  setAuthToken,
  setUnauthorizedHandler,
  setRateLimitHandler,
} from '@/shared/request'
import { STORAGE_KEYS } from '@/shared/utils/constants'
import { stripBasePath } from '@/shared/utils/path'
import { formatTrace } from '@/shared/utils/error-map'
import type { Role, CurrentUser } from '@/shared/types/common'

/**
 * 登录响应 data（来源：SSOT §5.0 A.1 认证契约）。
 * 前端不解析 JWT，角色/过期一律以 /me 与 401/403 为准。
 */
interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: CurrentUser
}

// ensureLoaded 的 in-flight Promise 持有者：并发路由跳转只发一次 /me
let fetchMeInFlight: Promise<void> | null = null

// 由 setupAuthHandlers 注入，供 logout 与未授权回调跳转使用
let appRouter: Router | null = null

/**
 * 认证 store（SSOT §10 / D8）。
 * 唯一 localStorage 读写点：仅 token 持久化（key ac_token）；
 * user / role 不持久化，由 /me 重建（防御「权限被回收但 token 未过期」）。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: null as string | null,
    user: null as CurrentUser | null,
    role: null as Role | null,
    loaded: false,
  }),

  actions: {
    /** 清空四字段 + 删 localStorage + 通知请求层清 token */
    clearAuth(): void {
      this.token = null
      this.user = null
      this.role = null
      this.loaded = false
      localStorage.removeItem(STORAGE_KEYS.TOKEN)
      setAuthToken(null)
    },

    /** 启动期：读 localStorage token，有则注入请求层并拉 /me 重建 user/role */
    initFromStorage(): void {
      const token = localStorage.getItem(STORAGE_KEYS.TOKEN)
      if (token) {
        this.token = token
        setAuthToken(token)
        // 有 token 才重建 user/role；走 ensureLoaded 复用 in-flight Promise，
        // 避免首屏 guard 再次触发 /me（spec：刷新页 /me 只发一次）。
        // 401 由拦截器 unauthorizedHandler 统一清态跳登录，此处不重复跳转。
        this.ensureLoaded().catch(() => {})
      }
    },

    /** 拉取 /me，以服务端为准重建 user/role */
    async fetchMe(): Promise<void> {
      // 响应拦截器已解包信封返回 data，故用第二泛型 R 覆盖返回类型为 CurrentUser
      const user = await http.get<CurrentUser, CurrentUser>('/api/v1/auth/me')
      this.user = user
      this.role = user.role
      this.loaded = true
    },

    /**
     * 首屏引导去重：loaded 直接返回；否则复用同一个 in-flight Promise。
     * 失败清态并抛出，由守卫统一跳登录。
     *
     * 无 token 时直接返回（S1）：spec R7 的括号说明是「首屏 **token 存在**
     * 但 user 为空时调 /me」。若不带 token 也发 /me，会先吃一个 401 触发
     * unauthorizedHandler 跳登录，与守卫自身的跳登录形成双导航竞态。
     */
    async ensureLoaded(): Promise<void> {
      if (this.loaded) return
      if (!this.token) return
      if (fetchMeInFlight) return fetchMeInFlight
      fetchMeInFlight = this.fetchMe().catch((err) => {
        this.clearAuth()
        throw err
      }).finally(() => {
        fetchMeInFlight = null
      })
      return fetchMeInFlight
    },

    /** 登录：写 token + user + role，持久化 token，注入请求层 */
    async login(username: string, password: string): Promise<void> {
      const data = await http.post<LoginResponse, LoginResponse>('/api/v1/auth/login', {
        username,
        password,
      })
      this.token = data.access_token
      this.user = data.user
      this.role = data.user.role
      setAuthToken(data.access_token)
      localStorage.setItem(STORAGE_KEYS.TOKEN, data.access_token)
      this.loaded = true
    },

    /** 登出：清态并跳登录页 */
    logout(): void {
      this.clearAuth()
      appRouter?.push('/login')
    },

    /** 角色判定：无角色或不在列表内返回 false */
    hasRole(...roles: Role[]): boolean {
      if (!this.role) return false
      return roles.includes(this.role)
    },
  },
})

/**
 * 注册请求层回调（未授权清态跳转 / 限流退避提示）。
 * 必须在 main.ts 中于 app.use(router) 之后调用。
 */
export function setupAuthHandlers(router: Router): void {
  appRouter = router

  // 401 / B0002：清登录态 → /login?redirect=<当前路径>
  setUnauthorizedHandler((redirectPath: string) => {
    const auth = useAuthStore()
    auth.clearAuth()
    // 地址栏路径含部署 base（/admin），必须先剥离再当 redirect 用，
    // 否则与 createWebHistory(base) 叠加成 /admin/admin/... → 白屏（B2 / spec R2）
    const internal = stripBasePath(redirectPath, router.options.history.base)
    router.push({ path: '/login', query: { redirect: internal } })
  })

  // 429 / B0006：读 Retry-After 提示退避，拦截器不自动重试（提示附 trace 后 8 位）
  setRateLimitHandler((retryAfterSeconds: number, trace?: string) => {
    const tip = retryAfterSeconds > 0
      ? `系统繁忙，请 ${retryAfterSeconds} 秒后重试`
      : '系统繁忙，稍后重试'
    ElMessage.warning(`${tip}（trace: ${formatTrace(trace)}）`)
  })
}
