import { defineStore } from 'pinia'
import type { Router } from 'vue-router'
import { ElMessage } from 'element-plus'
import http, {
  setAuthToken,
  setUnauthorizedHandler,
  setRateLimitHandler,
  setRefreshHandler,
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
  refresh_token: string
  token_type: string
  expires_in: number
  refresh_expires_in: number
  user: CurrentUser
}

// ensureLoaded 的 in-flight Promise 持有者：并发路由跳转只发一次 /me
let fetchMeInFlight: Promise<void> | null = null

// 由 setupAuthHandlers 注入，供 logout 与未授权回调跳转使用
let appRouter: Router | null = null

/**
 * 认证 store（SSOT §10 / D8 / frontend-security-hardening REQ-2）。
 * 唯一 sessionStorage 读写点：仅 token 持久化（key ac_token）；
 * user / role 不持久化，由 /me 重建（防御「权限被回收但 token 未过期」）。
 * 安全加固：从 localStorage 换 sessionStorage——会话级存储降低 XSS 接管
 * 爆炸半径（关闭 tab 自动清除）；刷新页面同 tab 保留，登录体验不变。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: null as string | null,
    refresh_token: null as string | null,
    user: null as CurrentUser | null,
    role: null as Role | null,
    loaded: false,
  }),

  actions: {
    /** 清空五字段 + 删 sessionStorage + 通知请求层清 token */
    clearAuth(): void {
      this.token = null
      this.refresh_token = null
      this.user = null
      this.role = null
      this.loaded = false
      sessionStorage.removeItem(STORAGE_KEYS.TOKEN)
      sessionStorage.removeItem(STORAGE_KEYS.REFRESH_TOKEN)
      setAuthToken(null)
    },

    /** 启动期：读 sessionStorage token，有则注入请求层并拉 /me 重建 user/role */
    initFromStorage(): void {
      const token = sessionStorage.getItem(STORAGE_KEYS.TOKEN)
      if (token) {
        this.token = token
        this.refresh_token = sessionStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN)
        setAuthToken(token)
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

    /** 登录：写双 token + user + role，持久化，注入请求层 */
    async login(username: string, password: string): Promise<void> {
      const data = await http.post<LoginResponse, LoginResponse>('/api/v1/auth/login', {
        username,
        password,
      })
      this.token = data.access_token
      this.refresh_token = data.refresh_token
      this.user = data.user
      this.role = data.user.role
      setAuthToken(data.access_token)
      sessionStorage.setItem(STORAGE_KEYS.TOKEN, data.access_token)
      sessionStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, data.refresh_token)
      this.loaded = true
    },

    /** Refresh Token 换 access_token（401 自动刷新链路调用） */
    async refreshAccessToken(): Promise<string | null> {
      if (!this.refresh_token) return null
      try {
        const data = await http.post<{ access_token: string }, { access_token: string }>(
          '/api/v1/auth/refresh',
          undefined,
          {
            headers: { Authorization: `Bearer ${this.refresh_token}` },
            // 标记跳过拦截器注入——refresh_token 端点自带 Bearer，
            // 不能让 authToken（过期的 access_token）覆盖掉 refresh_token
            _skipAuthInterceptor: true,
          },
        )
        this.token = data.access_token
        setAuthToken(data.access_token)
        sessionStorage.setItem(STORAGE_KEYS.TOKEN, data.access_token)
        return data.access_token
      } catch {
        // refresh 失败（过期 / 被吊销 / 类型错）→ 清态交给外层 unauthorizedHandler 跳登录
        this.clearAuth()
        return null
      }
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

  // 401 / B0002：Refresh Token 换新 access_token（拦截器重放原请求）
  setRefreshHandler(() => useAuthStore().refreshAccessToken())
}
