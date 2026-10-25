/**
 * 通用异步任务三态（SSOT §19.4）。
 *
 * 零业务语义：只负责 loading / error / data 三态与「后发先至」竞态保护。
 * - run() 每次自增序号，过期响应（序号不匹配）直接丢弃，避免快速切换时旧数据覆盖新数据；
 * - error 统一归一为 Error，页面只消费 message；
 * - immediate（默认 true）在 onMounted 触发一次；refetchOnActivated 在 keepAlive 再次激活时刷新，
 *   首次 activated 跳过（首次挂载已由 onMounted 负责），避免重复请求。
 */
import { onActivated, onMounted, ref, type Ref } from 'vue'

export interface UseAsyncTaskOptions {
  /** 挂载即执行一次，默认 true */
  immediate?: boolean
  /** keepAlive 再次激活时刷新，默认 false */
  refetchOnActivated?: boolean
}

export interface UseAsyncTaskResult<T> {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<Error | null>
  run: () => Promise<T | null>
  retry: () => Promise<T | null>
  reset: () => void
}

export function useAsyncTask<T>(
  runner: () => Promise<T>,
  options: UseAsyncTaskOptions = {},
): UseAsyncTaskResult<T> {
  const data = ref(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref(null) as Ref<Error | null>

  // 请求序号：只允许最后一次请求写回状态
  let seq = 0

  async function run(): Promise<T | null> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const result = await runner()
      if (current !== seq) return null
      data.value = result
      return result
    } catch (e) {
      if (current !== seq) return null
      error.value = e instanceof Error ? e : new Error(String(e))
      return null
    } finally {
      if (current === seq) loading.value = false
    }
  }

  function reset(): void {
    seq++ // 作废在途请求
    data.value = null
    error.value = null
    loading.value = false
  }

  if (options.immediate !== false) {
    onMounted(() => {
      void run()
    })
  }

  if (options.refetchOnActivated) {
    let firstActivated = true
    onActivated(() => {
      if (firstActivated) {
        firstActivated = false
        return
      }
      void run()
    })
  }

  return { data, loading, error, run, retry: run, reset }
}
