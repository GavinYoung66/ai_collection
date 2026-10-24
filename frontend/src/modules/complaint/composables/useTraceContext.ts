/**
 * useTraceContext composable（tasks 3.5② / SSOT §9.4）。
 *
 * 契约：
 * - 详情页 setup 顶层生成 32 位 hex trace_id → setTraceContext 写入（拦截器注入 X-Trace-Id 头）
 * - onScopeDispose 自动 setTraceContext(null) 清空
 * - 业务代码禁止手写 X-Trace-Id 请求头（拦截器统一处理）
 * - trace_id 用 shared/utils/uuid.randomTraceId 生成，对齐后端 32 位 hex 格式（uuid4 hex）
 */
import { onScopeDispose, ref } from 'vue'
import { getTraceContext, setTraceContext } from '@/shared/request'
import { randomTraceId } from '@/shared'

export interface UseTraceContextResult {
  /** 当前 trace_id（业务只读展示用，禁止直接操作请求头） */
  traceId: ReturnType<typeof ref<string | null>>
  /** 手动写入新 trace_id（例如串接外部链路） */
  set: (id?: string) => string
  /** 手动清空 */
  clear: () => void
  /** 当前 request.ts 里存的 trace context（拦截器读取） */
  readonly fromRequest: string | null
}

export function useTraceContext(): UseTraceContextResult {
  const traceId = ref<string | null>(null)

  function write(id?: string): string {
    const value = id ?? randomTraceId()
    traceId.value = value
    setTraceContext(value)
    return value
  }

  function clear(): void {
    traceId.value = null
    setTraceContext(null)
  }

  // setup 顶层立即写入（组件实例创建后拦截器已就绪），卸载时自动清空
  write()
  onScopeDispose(() => clear())

  return {
    traceId,
    set: write,
    clear,
    get fromRequest() { return getTraceContext() },
  }
}
