import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { Router, RouteLocationNormalized } from 'vue-router'
import type { Role } from '@/shared/types/common'

// 隔离网络：mock shared/request 的 http 与回调注册
// vi.hoisted 保证 mock 工厂执行时变量已初始化（避免 TDZ）
const { mockHttpGet, mockHttpPost } = vi.hoisted(() => ({
  mockHttpGet: vi.fn(),
  mockHttpPost: vi.fn(),
}))

vi.mock('@/shared/request', () => ({
  default: { get: mockHttpGet, post: mockHttpPost },
  setAuthToken: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
  setRateLimitHandler: vi.fn(),
  TIMEOUT: { normal: 12_000, judge: 110_000 },
}))

// 用真实 auth store（pinia）+ mock http 验证 ensureLoaded 去重
import { useAuthStore } from '@/app/stores/auth'
import { setupGuards, firstAccessibleRoute, isValidRedirect } from '@/app/router/guards'

// 构造 mock router，捕获 beforeEach 注册的守卫函数
let capturedGuard: ((to: RouteLocationNormalized) => unknown) | null = null
const mockRouter = {
  beforeEach: vi.fn((guard: (to: RouteLocationNormalized) => unknown) => {
    capturedGuard = guard
  }),
  push: vi.fn(),
} as unknown as Router

// 构造路由目标对象
function toObj(path: string, meta: Record<string, unknown> = {}): RouteLocationNormalized {
  return { path, fullPath: path, meta } as unknown as RouteLocationNormalized
}

describe('路由守卫与落点', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockHttpGet.mockReset()
    mockHttpPost.mockReset()
    setActivePinia(createPinia())
    capturedGuard = null
    setupGuards(mockRouter)
  })

  it('1. 未登录跳 /login?redirect=<目标完整路径>', async () => {
    const auth = useAuthStore()
    auth.token = null
    auth.role = null
    auth.loaded = true

    const result = await capturedGuard!(toObj('/complaints'))
    expect(result).toEqual({ path: '/login', query: { redirect: '/complaints' } })
  })

  it('2. 越权（role 不在 meta.roles）跳 /403', async () => {
    const auth = useAuthStore()
    auth.token = 't'
    auth.role = 'agent'
    auth.loaded = true

    const result = await capturedGuard!(toObj('/system/users', { roles: ['admin'] }))
    expect(result).toBe('/403')
  })

  it('3. redirect 仅接受站内相对路径', () => {
    expect(isValidRedirect('/complaints')).toBe(true)
    expect(isValidRedirect('/qc/tasks')).toBe(true)
    // 非法：非 / 开头、或 // 开头（协议相对 / 外站）
    expect(isValidRedirect('complaints')).toBe(false)
    expect(isValidRedirect('//evil.com')).toBe(false)
    expect(isValidRedirect('https://evil.com')).toBe(false)
    expect(isValidRedirect('')).toBe(false)
  })

  it('4. 无 redirect 按角色落点（禁止全写死 /complaints）', () => {
    expect(firstAccessibleRoute('admin')).toBe('/complaints')
    expect(firstAccessibleRoute('supervisor')).toBe('/complaints')
    expect(firstAccessibleRoute('agent')).toBe('/complaints')
    expect(firstAccessibleRoute('qc_reviewer')).toBe('/qc/tasks')
    expect(firstAccessibleRoute('ai_trainer')).toBe('/kb/documents')
    // 无角色 → /403
    expect(firstAccessibleRoute(null)).toBe('/403')
  })

  it('5. ensureLoaded 并发只发一次 /me（in-flight 去重）', async () => {
    const meUser = { user_id: 'u1', username: 'admin', real_name: '管', role: 'admin' as Role, status: 'active' as const }
    // /me 返回一个延迟 Promise，确保两次 ensureLoaded 命中同一 in-flight
    mockHttpGet.mockImplementationOnce(
      () => new Promise((resolve) => setTimeout(() => resolve(meUser), 10)),
    )

    const auth = useAuthStore()
    auth.token = 't'
    auth.loaded = false

    const p1 = auth.ensureLoaded()
    const p2 = auth.ensureLoaded()
    await Promise.all([p1, p2])

    // 两次并发只发一次 /me
    expect(mockHttpGet).toHaveBeenCalledTimes(1)
    expect(mockHttpGet).toHaveBeenCalledWith('/api/v1/auth/me')
    expect(auth.user).toEqual(meUser)
    expect(auth.role).toBe('admin')
    expect(auth.loaded).toBe(true)
  })

  it('6. role 为空不递归重定向（直接落 /403，不计算 firstAccessibleRoute）', async () => {
    const auth = useAuthStore()
    auth.token = 't'
    auth.role = null
    auth.loaded = true

    const result = await capturedGuard!(toObj('/complaints'))
    // role 为空 → /403，而非重定向到角色落点（防 / ↔ /403 死循环）
    expect(result).toBe('/403')
  })

  it('6-bis. /login 与 /403 直接放行，不触发 ensureLoaded', async () => {
    const auth = useAuthStore()
    auth.token = null
    auth.loaded = false

    // /login 放行
    const r1 = await capturedGuard!(toObj('/login'))
    expect(r1).toBe(true)
    // /403 放行
    const r2 = await capturedGuard!(toObj('/403'))
    expect(r2).toBe(true)
    // 放行路径不应调用 /me
    expect(mockHttpGet).not.toHaveBeenCalled()
  })

  it('7. 无 token 时不发 /me，直接跳登录（避免 401 双导航竞态）', async () => {
    const auth = useAuthStore()
    auth.token = null
    auth.role = null
    auth.loaded = false

    const result = await capturedGuard!(toObj('/complaints'))

    // 未带 token 不应请求 /me（spec R7：「token 存在但 user 为空时」才调 /me）
    expect(mockHttpGet).not.toHaveBeenCalled()
    expect(result).toEqual({ path: '/login', query: { redirect: '/complaints' } })
  })

  it('8. 根路径按角色落点重定向（避免空布局白页）', async () => {
    const auth = useAuthStore()
    auth.token = 't'
    auth.role = 'qc_reviewer'
    auth.loaded = true

    const result = await capturedGuard!(toObj('/'))
    expect(result).toBe('/qc/tasks')
  })
})
