/**
 * useQcTraceContext（f2-qc-console 批次4 / tasks 5.5，design D5）。
 *
 * 照搬 complaint 的 useTraceContext 模式，复用 shared/request.ts 已落地的
 * X-Trace-Id 拦截器注入逻辑（注入代码零改动）：
 * - 详情页 setup 时生成 trace_id → setTraceContext 写入（拦截器自动注入请求头）
 * - 组件卸载（onScopeDispose）时 setTraceContext(null) 清空
 * - 业务代码禁止手写 X-Trace-Id 请求头
 * - 串联详情页「查看 → 开检 → 复核提交」复合链路：同一挂载周期内同一 X-Trace-Id
 */
import { onScopeDispose, ref } from 'vue'
import { getTraceContext, setTraceContext } from '@/shared/request'
import { randomTraceId } from '@/shared'

export interface UseQcTraceContextResult {
  /** 当前 trace_id（只读展示用，禁止直接操作请求头） */
  traceId: ReturnType<typeof ref<string | null>>
  /** 手动写入新 trace_id */
  set: (id?: string) => string
  /** 手动清空 */
  clear: () => void
  /** 当前 request.ts 里存的 trace context（拦截器读取） */
  readonly fromRequest: string | null
}

export function useQcTraceContext(): UseQcTraceContextResult {
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
