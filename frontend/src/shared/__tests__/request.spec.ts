import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'

// 拦截 axios.create 返回的实例，捕获注册的拦截器函数
const mockRequestUse = vi.fn()
const mockResponseUse = vi.fn()

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      interceptors: {
        request: { use: mockRequestUse },
        response: { use: mockResponseUse },
      },
      defaults: { baseURL: '/api', timeout: 12000 },
    })),
  },
}))

// 简易 headers 替身，支持 set/get
function mockHeaders(initial: Record<string, string> = {}) {
  const store: Record<string, string> = { ...initial }
  return {
    set(key: string, value: string) {
      store[key] = value
    },
    get(key: string) {
      return store[key]
    },
  }
}

let requestFulfilled:
  | ((config: InternalAxiosRequestConfig) => InternalAxiosRequestConfig | Promise<InternalAxiosRequestConfig>)
  | undefined
let responseFulfilled: ((response: AxiosResponse) => unknown) | undefined
let responseRejected: ((error: AxiosError) => unknown) | undefined

// 动态重导 request.ts 以重置模块级状态（token / handlers）
async function loadRequest() {
  vi.resetModules()
  const mod = await import('@/shared/request')
  requestFulfilled = mockRequestUse.mock.calls.at(-1)?.[0]
  responseFulfilled = mockResponseUse.mock.calls.at(-1)?.[0]
  responseRejected = mockResponseUse.mock.calls.at(-1)?.[1]
  return mod
}

// 构造模拟响应并断言为 AxiosResponse（测试用，字段按需提供）
function asResponse(obj: Record<string, unknown>): AxiosResponse {
  return obj as unknown as AxiosResponse
}

describe('shared/request 请求层', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('1. 信封成功解包返回 data', async () => {
    const req = await loadRequest()
    req.setAuthToken(null)
    const data = await responseFulfilled!(
      asResponse({
        config: {},
        status: 200,
        data: { code: 0, message: 'ok', data: { foo: 'bar' }, trace_id: 'tr_12345678' },
        headers: {},
      }),
    )
    expect(data).toEqual({ foo: 'bar' })
  })

  it('2. 401 触发未授权回调并跳登录', async () => {
    const req = await loadRequest()
    const handler = vi.fn()
    req.setUnauthorizedHandler(handler)

    await expect(
      responseFulfilled!(
        asResponse({
          config: { url: '/api/v1/complaints', method: 'get' },
          status: 401,
          data: { code: 'B0002', message: '请先登录', data: null, trace_id: 'tr_aaaabbbb' },
          headers: {},
        }),
      ),
    ).rejects.toThrow('请先登录')

    expect(handler).toHaveBeenCalledWith(window.location.pathname + window.location.search)
  })

  it('3. POST /api/v1/auth/login 自身 401 不清态、不跳登录', async () => {
    const req = await loadRequest()
    const handler = vi.fn()
    req.setUnauthorizedHandler(handler)

    await expect(
      responseFulfilled!(
        asResponse({
          config: { url: '/api/v1/auth/login', method: 'post' },
          status: 401,
          data: { code: 'B0002', message: '用户名或密码错误', data: null, trace_id: 'tr_11112222' },
          headers: {},
        }),
      ),
    ).rejects.toThrow('用户名或密码错误')

    expect(handler).not.toHaveBeenCalled()
  })

  it('4. 非 0 code 提示附 trace 后 8 位', async () => {
    await loadRequest()
    await expect(
      responseFulfilled!(
        asResponse({
          config: {},
          status: 200,
          data: { code: 'A5001', message: '投诉工单不存在', data: null, trace_id: 'tr_0123456789abcdef' },
          headers: {},
        }),
      ),
    ).rejects.toThrow(/投诉工单不存在.*89abcdef/)
  })

  it('5. 429 走白名单且不自动重试（仅触发限流回调）', async () => {
    const req = await loadRequest()
    const rateHandler = vi.fn()
    req.setRateLimitHandler(rateHandler)

    await expect(
      responseFulfilled!(
        asResponse({
          config: {},
          status: 429,
          data: { code: 'B0006', message: '请求过于频繁', data: null, trace_id: 'tr_ccccdddd' },
          headers: { 'retry-after': '5' },
        }),
      ),
    ).rejects.toThrow(/系统繁忙.*ccccdddd/)

    // 429 回调必须带上 trace，供提示文案附后 8 位（spec R2 的 429 Scenario）
    expect(rateHandler).toHaveBeenCalledWith(5, 'tr_ccccdddd')
  })

  it('6. 非信封响应走兜底且 trace 显示「无」', async () => {
    await loadRequest()
    await expect(
      responseFulfilled!(
        asResponse({
          config: {},
          status: 200,
          data: '<html>404 Not Found</html>',
          headers: {},
        }),
      ),
    ).rejects.toThrow(/响应格式异常.*trace: 无/)
  })

  it('7. token 为空时请求不注入 Authorization', async () => {
    const req = await loadRequest()
    req.setAuthToken(null)

    const headers = mockHeaders()
    await requestFulfilled!({ headers } as unknown as InternalAxiosRequestConfig)

    expect(headers.get('Authorization')).toBeUndefined()
  })

  it('7-bis. token 非空时注入 Bearer', async () => {
    const req = await loadRequest()
    req.setAuthToken('token-xyz')

    const headers = mockHeaders()
    await requestFulfilled!({ headers } as unknown as InternalAxiosRequestConfig)

    expect(headers.get('Authorization')).toBe('Bearer token-xyz')
  })

  it('8. 网络层错误（超时/断连）走兜底', async () => {
    await loadRequest()
    await expect(
      responseRejected!(new Error('timeout') as unknown as AxiosError),
    ).rejects.toThrow(/网络异常.*trace: 无/)
  })

  it('9. 非白名单端点即使 responseType:text 也走信封解析（白名单不可被放宽）', async () => {
    await loadRequest()
    await expect(
      responseFulfilled!(
        asResponse({
          config: { url: '/api/v1/complaints', responseType: 'text' },
          status: 200,
          data: { code: 'A5001', message: '投诉工单不存在', data: null, trace_id: 'tr_01234567' },
          headers: {},
        }),
      ),
    ).rejects.toThrow(/投诉工单不存在/)
  })

  it('10. 白名单端点 text 响应 404 映射为空态', async () => {
    await loadRequest()
    const result = await responseFulfilled!(
      asResponse({
        config: { url: '/api/v1/sensitive-words/current.dict', responseType: 'text' },
        status: 404,
        data: '',
        headers: {},
      }),
    )
    expect(result).toBe('')
  })

  it('11. trace 上下文非空时请求头注入 X-Trace-Id（§9.4 F1）', async () => {
    const req = await loadRequest()
    req.setTraceContext('tr_detail_12345678')

    const headers = mockHeaders()
    await requestFulfilled!({ headers } as unknown as InternalAxiosRequestConfig)

    expect(headers.get('X-Trace-Id')).toBe('tr_detail_12345678')
  })

  it('12. trace 上下文无值或清空后不注入 X-Trace-Id', async () => {
    const req = await loadRequest()

    // 从未写入：不注入
    const freshHeaders = mockHeaders()
    await requestFulfilled!({ headers: freshHeaders } as unknown as InternalAxiosRequestConfig)
    expect(freshHeaders.get('X-Trace-Id')).toBeUndefined()

    // 写入后清空：不注入（离开详情页即清空，不污染列表类请求）
    req.setTraceContext('tr_detail_12345678')
    req.setTraceContext(null)
    const clearedHeaders = mockHeaders()
    await requestFulfilled!({ headers: clearedHeaders } as unknown as InternalAxiosRequestConfig)
    expect(clearedHeaders.get('X-Trace-Id')).toBeUndefined()
  })
})
