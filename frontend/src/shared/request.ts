import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { isSuccess, type ApiEnvelope } from './types/envelope'
import { getErrorMessage, formatTrace } from './utils/error-map'

/**
 * 请求层抛出的业务错误：在 Error 基础上附加 code（错误码）与 trace（trace_id）。
 * 登录页等场景需按 code 区分 B0002（凭证错误）与 B0003（账号停用），
 * 故拦截器统一附加这两个字段，不改变 message 文案（SSOT §15.1 / D5）。
 */
export interface RequestError extends Error {
  code?: string
  trace?: string
}

/** 构造带 code / trace 的 Error，保持原有 message 不变 */
function makeError(message: string, code?: string | number, trace?: string): RequestError {
  const err = new Error(message) as RequestError
  if (code !== undefined) err.code = String(code)
  if (trace) err.trace = trace
  return err
}

/**
 * 超时常量表（SSOT §9.3）：前端超时 < 网关 proxy_read_timeout。
 * normal 12s（默认）/ judge 110s（投诉研判）/ qcReplay 110s（F2：qc replay/{session_id}
 * 与 check/batch，网关对应正则 location 为 120s，留 10s 余量，design D1）。
 * 不设 upload 常量（后端放宽 ≥60s 前上传沿用 normal）。
 * api 层按端点引用常量，禁止散写数值。
 */
export const TIMEOUT = {
  normal: 12_000,
  judge: 110_000,
  qcReplay: 110_000,
} as const

// 模块级 token holder：由 auth store 通过 setAuthToken 写入。
// request.ts 只持内存 token、不读写 localStorage（auth store 是唯一存储读写点）。
let authToken: string | null = null

/**
 * 未授权处理回调：由批次 3 的 auth store 注册（清登录态 + 跳 /login?redirect=）。
 * 用回调而非直接 import app/stores，避免 shared → app 循环依赖（SSOT §9.1 / D5）。
 */
type UnauthorizedHandler = (redirectPath: string) => void
let unauthorizedHandler: UnauthorizedHandler | null = null

/**
 * 限流处理回调：由批次 3 注册（读 Retry-After 提示退避，拦截器不自动重试）。
 * trace 用于在提示文案中附 trace 后 8 位（spec R2 的 429 Scenario）。
 */
type RateLimitHandler = (retryAfterSeconds: number, trace?: string) => void
let rateLimitHandler: RateLimitHandler | null = null

/** 写入/清空内存 token（auth store 在 login/logout/initFromStorage 时调用） */
export function setAuthToken(token: string | null): void {
  authToken = token
}

/** 注册未授权处理回调（auth store 调用）；传 null 可注销 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

/** 注册限流处理回调（auth store 或 composable 调用）；传 null 可注销 */
export function setRateLimitHandler(handler: RateLimitHandler | null): void {
  rateLimitHandler = handler
}

// 模块级 trace 上下文（spec R13：MUST 预留读写函数供 F1 使用）。
// F1 起请求拦截器在上下文非空时注入 X-Trace-Id（§9.4：详情页复合链路串联）；
// 上下文的生成/清空由模块侧 composable 负责（F1 ComplaintDetail），本文件不生成。
let traceContext: string | null = null

/** 写入当前 trace 上下文（F1 详情页挂载时写入、卸载时清空） */
export function setTraceContext(traceId: string | null): void {
  traceContext = traceId
}

/** 读取当前 trace 上下文（F1 使用；F0 仅预留扩展点） */
export function getTraceContext(): string | null {
  return traceContext
}

/** 非信封白名单：唯一允许返回裸文本的端点（spec R2） */
const TEXT_WHITELIST = '/api/v1/sensitive-words/current.dict'

/** 全工程唯一 axios 实例（SSOT §9.1 / D5） */
const http: AxiosInstance = axios.create({
  // F0 约定 baseURL 为空串（api 路径自带 /api/v1 前缀）；
  // CI 干净环境无 .env，此处取不到值时为 undefined，故显式兜底为 ''
  baseURL: import.meta.env.VITE_API_BASE ?? '',
  timeout: TIMEOUT.normal,
  // 所有 HTTP 状态码统一走成功分支，由响应拦截器按信封解析（网关 401/429/502/504 也返回信封）
  validateStatus: () => true,
})

// 请求拦截器：注入 Bearer token；token 为空时不注入（禁止 Bearer null）
// 特殊标记 _skipAuthInterceptor=true 时跳过注入（refresh_token 端点自带 Bearer）
http.interceptors.request.use((config: InternalAxiosRequestConfig & { _skipAuthInterceptor?: boolean }) => {
  if (authToken && !config._skipAuthInterceptor) {
    config.headers.set('Authorization', `Bearer ${authToken}`)
  }
  // _skipAuthInterceptor 是业务内部标记，不下发；删除避免透传到后端
  delete config._skipAuthInterceptor
  // X-Trace-Id 注入（§9.4 F1 落地）
  const ctx = getTraceContext()
  if (ctx) {
    config.headers.set('X-Trace-Id', ctx)
  }
  return config
})

// 响应拦截器：统一信封解析
http.interceptors.response.use(
  async (response: AxiosResponse) => {
    const { config, status, data, headers } = response

    // —— 非信封白名单：仅 GET /api/v1/sensitive-words/current.dict 走 text ——
    if (config.responseType === 'text' && (config.url ?? '').includes(TEXT_WHITELIST)) {
      if (status === 404) return ''
      return data
    }

    const body = data as ApiEnvelope<unknown>
    const isEnvelope =
      body !== null &&
      typeof body === 'object' &&
      'code' in body &&
      'message' in body &&
      'data' in body

    if (!isEnvelope) {
      const trace = (headers['x-trace-id'] as string | undefined) || undefined
      return Promise.reject(makeError(`响应格式异常（trace: ${formatTrace(trace)}）`, undefined, trace))
    }

    const { code, message, trace_id } = body
    const trace = (headers['x-trace-id'] as string | undefined) || trace_id

    if (isSuccess(code)) {
      return body.data
    }

    // 401 或 B0002：尝试 Refresh Token 自动刷新 → 重放原请求
    if (status === 401 || code === 'B0002') {
      const isLoginSelf =
        (config.url ?? '').includes('/api/v1/auth/login') &&
        (config.method ?? 'get').toUpperCase() === 'POST'
      if (!isLoginSelf) {
        try {
          // ── backend-security-completion REQ-5c：Refresh Token 自动刷新 ──
          // 懒加载 auth store（shared → app 单向，模块顶层不能 import）
          const { useAuthStore } = await import('../app/stores/auth')
          const auth = useAuthStore()
          const newToken = await auth.refreshAccessToken()
          if (newToken) {
            return http.request(config)
          }
        } catch {
          // refresh 抛错（auth store 已内部清态）：fallthrough 到 unauthorizedHandler
        }
        const redirectPath = window.location.pathname + window.location.search
        unauthorizedHandler?.(redirectPath)
      }
      return Promise.reject(makeError(message, code, trace))
    }

    // 429 或 B0006：读 Retry-After 提示退避，拦截器不自动重试
    if (status === 429 || code === 'B0006') {
      const retryAfter = Number(headers['retry-after']) || 0
      rateLimitHandler?.(retryAfter, trace)
      const displayMessage = getErrorMessage(code, message)
      return Promise.reject(
        makeError(`${displayMessage}（trace: ${formatTrace(trace)}）`, code, trace),
      )
    }

    // 其他非成功 code：走 error-map 白名单，提示附 trace 后 8 位
    const displayMessage = getErrorMessage(code, message)
    const finalError = makeError(`${displayMessage}（trace: ${formatTrace(trace)}）`, code, trace)
    // fire-and-forget：调度到 errorReporters（后端 audit/frontend-error 端点）
    _dispatchError(finalError)
    return Promise.reject(finalError)
  },
  (_error: AxiosError) => {
    return Promise.reject(makeError('网络异常，请稍后重试（trace: 无）'))
  },
)

export default http

// ── backend-security-completion REQ-5a：前端错误上报钩子 ──
// 收集器：main.ts 初始化时桥接全局 window.onerror / unhandledrejection
// + app.config.errorHandler 到此数组，每个错误都会被遍历调用。
// fire-and-forget：hook 内部应自行 try/catch 吞掉（审计失败绝不阻塞 UI）。
type ErrorReporter = (err: RequestError) => void
const errorReporters: ErrorReporter[] = []
export function registerErrorReporter(fn: ErrorReporter): void {
  errorReporters.push(fn)
}
// 请求层 401/非零 code 错误发生时遍历（interceptor 内调用）
export function _dispatchError(err: RequestError): void {
  for (const hook of errorReporters) {
    try { hook(err) } catch { /* 审计失败静默吞 */ }
  }
}
