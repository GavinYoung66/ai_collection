/**
 * useTraceContext composable（tasks 3.5② / SSOT §9.4）。
 *
 * 契约：
 * - 详情页 onMounted 生成 trace_id → setTraceContext 写入（request.ts 请求拦截器会自动注入 X-Trace-Id 头）
 * - onUnmounted setTraceContext(null) 清空
 * - 业务代码**禁止手写** X-Trace-Id 请求头（拦截器统一处理）
 * - trace_id 生成带 crypto.randomUUID 兼容性 fallback（vitest/jsdom 可能无该 API）
 */
import { onScopeDispose, ref } from 'vue'
import { getTraceContext, setTraceContext } from '@/shared/request'

function generateTraceId(): string {
  // crypto.randomUUID 在 jsdom/vitest 下可能不存在
  const c = globalThis.crypto as undefined | { randomUUID?: () => string }
  if (c?.randomUUID) return c.randomUUID()
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
}

export interface UseTraceContextOptions {
  /** 可选：在哪个生命周期写入（默认 'mounted'） */
  writeAt?: 'mounted' | 'manual'
}

export interface UseTraceContextResult {
  /** 当前 trace_id（业务只读展示用，禁止直接操作） */
  traceId: ReturnType<typeof ref<string | null>>
  /** 手动写入新 trace_id（writeAt='manual' 时可用） */
  set: (id?: string) => string
  /** 手动清空 */
  clear: () => void
  /** 当前 request.ts 里存的 trace context（拦截器读取） */
  readonly fromRequest: string | null
}

/**
 * 详情页 trace 上下文管理。
 *
 * 用法（ComplaintDetail.vue）：
 * ```ts
 * const { traceId } = useTraceContext()
 * // 自动在 mounted 时生成并写入，unmounted 时清空
 * ```
 */
export function useTraceContext(options: UseTraceContextOptions = {}): UseTraceContextResult {
  const { writeAt = 'mounted' } = options
  const traceId = ref<string | null>(null)

  function write(id?: string): string {
    const value = id ?? generateTraceId()
    traceId.value = value
    setTraceContext(value)
    return value
  }

  function clear(): void {
    traceId.value = null
    setTraceContext(null)
  }

  // 写入时机
  if (writeAt === 'mounted') {
    // onScopeDispose 会在 setup 的同步阶段之后、组件挂载后生效；
    // 这里用 flushPromises 的等效方式：直接写（setup 顶层立即执行，而组件实例创建后拦截器已就绪）
    write()
  }

  // 自动清理（Composition API setup 上下文可用时）
  onScopeDispose(() => clear())

  return {
    traceId,
    set: write,
    clear,
    get fromRequest() { return getTraceContext() },
  }
}
