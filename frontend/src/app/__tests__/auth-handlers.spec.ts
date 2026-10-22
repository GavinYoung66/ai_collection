// @vitest-environment jsdom
/**
 * 认证回调注册的行为验证（B2 / B3 回归）：
 * 直接调用 setupAuthHandlers 注册到请求层的两个回调，断言其对外行为，
 * 而不是只测纯函数——因为缺陷正出在「回调把地址栏路径原样当 redirect」这一步。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const captured = {
  unauthorized: null as ((p: string) => void) | null,
  rateLimit: null as ((s: number, trace?: string) => void) | null,
}

vi.mock('@/shared/request', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  setAuthToken: vi.fn(),
  setUnauthorizedHandler: (h: ((p: string) => void) | null) => {
    captured.unauthorized = h
  },
  setRateLimitHandler: (h: ((s: number, trace?: string) => void) | null) => {
    captured.rateLimit = h
  },
  TIMEOUT: { normal: 12_000, judge: 110_000 },
}))

vi.mock('element-plus', () => ({ ElMessage: { warning: vi.fn() } }))

import { createPinia, setActivePinia } from 'pinia'
import { ElMessage } from 'element-plus'
import { setupAuthHandlers, useAuthStore } from '@/app/stores/auth'

/** 构造带 history.base 的最小 router 替身（base='/admin' 模拟部署前缀） */
function makeRouter(base = '/admin') {
  return {
    push: vi.fn(),
    options: { history: { base } },
  } as unknown as Parameters<typeof setupAuthHandlers>[0]
}

describe('auth 请求层回调注册', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('1. 401 回调剥离部署 base 后再当 redirect（回归 B2）', () => {
    const router = makeRouter('/admin')
    setupAuthHandlers(router)
    const auth = useAuthStore()
    auth.token = 't'
    auth.role = 'admin'
    auth.loaded = true

    captured.unauthorized!('/admin/complaints?page=2')

    expect(router.push).toHaveBeenCalledWith({
      path: '/login',
      query: { redirect: '/complaints?page=2' },
    })
    // 清态生效
    expect(auth.token).toBeNull()
    expect(auth.loaded).toBe(false)
  })

  it('2. 429 提示附 trace 后 8 位（回归 B3）', () => {
    const router = makeRouter('/admin')
    setupAuthHandlers(router)

    captured.rateLimit!(5, 'tr_ccccdddd')
    expect(ElMessage.warning).toHaveBeenCalledWith('系统繁忙，请 5 秒后重试（trace: ccccdddd）')

    // trace 缺失时显示「无」，不抛异常
    captured.rateLimit!(0)
    expect(ElMessage.warning).toHaveBeenCalledWith('系统繁忙，稍后重试（trace: 无）')
  })
})
