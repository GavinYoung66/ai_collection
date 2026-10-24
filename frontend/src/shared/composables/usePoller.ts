/**
 * 通用轮询调度引擎（SSOT §19.5）。
 *
 * 只负责「什么时候发起下一次」，不感知任何业务字段：
 * - 间隔策略由调用方以 `nextInterval(result, currentMs)` 注入（含退避阶梯）；
 *   终态谓词 `isTerminal` 为真时停止并回调；
 * - 连续失败达到 `maxFailures` 熔断，暴露 error/errorTrace 供 UI 展示；
 * - `document.hidden` 暂停并清定时器，恢复可见时立即补一次并重排定时器
 *   （若只补一次不重排，隐藏再恢复后定时器已不存在，轮询会静默死亡）；
 * - `onScopeDispose` 清理定时器与监听器；
 * - `start()` 允许在 `stop()` 之后重启（页面重新激活、任务重新在途时需要）。
 */
import { onScopeDispose, ref, type Ref } from 'vue'

export interface UsePollerOptions<T> {
  /** 单次轮询：返回一次快照；抛错计入连续失败 */
  poll: () => Promise<T>
  /** 终态谓词：返回 true 时停止轮询 */
  isTerminal: (result: T) => boolean
  /** 间隔策略：入参为本次快照与当前间隔，返回下一次间隔 ms（默认恒定为 intervalMs） */
  nextInterval?: (result: T, currentInterval: number) => number
  /** 基准间隔 ms，默认 3000 */
  intervalMs?: number
  /** 连续失败熔断次数，默认 3 */
  maxFailures?: number
  /** 到达终态回调（最后一次快照） */
  onTerminal?: (result: T) => void
  /** 熔断回调（message 为拦截器已加工文案，trace 为链路 id） */
  onError?: (message: string, trace?: string) => void
}

export interface UsePollerResult<T> {
  isPolling: Ref<boolean>
  isStopped: Ref<boolean>
  /** 最近一次成功快照（终态值也会写入） */
  lastResult: Ref<T | null>
  error: Ref<string | null>
  errorTrace: Ref<string | null>
  start: () => Promise<void>
  stop: () => void
  pollNow: () => Promise<void>
}

export function usePoller<T>(options: UsePollerOptions<T>): UsePollerResult<T> {
  const baseInterval = options.intervalMs ?? 3000
  const maxFailures = options.maxFailures ?? 3

  const isPolling = ref(false)
  const isStopped = ref(false)
  const lastResult = ref(null) as Ref<T | null>
  const error = ref<string | null>(null)
  const errorTrace = ref<string | null>(null)

  let timerId: ReturnType<typeof setInterval> | null = null
  let currentInterval = baseInterval
  let consecutiveFailures = 0
  let isPaused = false
  let listenerAttached = false

  function clearTimer(): void {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
  }

  function scheduleNext(intervalMs: number): void {
    clearTimer()
    currentInterval = intervalMs
    timerId = setInterval(() => {
      void tick()
    }, intervalMs)
  }

  /** 统一停止收口：终态自停 / 熔断 / 外部 stop 共用 */
  function finish(): void {
    isPolling.value = false
    isStopped.value = true
    clearTimer()
    unbindVisibility()
  }

  async function tick(): Promise<void> {
    if (isPaused || isStopped.value) return
    try {
      const result = await options.poll()
      consecutiveFailures = 0
      error.value = null
      errorTrace.value = null
      lastResult.value = result

      if (options.isTerminal(result)) {
        finish()
        options.onTerminal?.(result)
        return
      }

      const next = options.nextInterval
        ? options.nextInterval(result, currentInterval)
        : baseInterval
      if (next !== currentInterval) {
        scheduleNext(next)
      }
    } catch (err: unknown) {
      const e = err as { message?: string; trace?: string }
      consecutiveFailures++
      if (consecutiveFailures >= maxFailures) {
        error.value = e?.message ?? '轮询失败'
        errorTrace.value = e?.trace ?? null
        finish()
        options.onError?.(error.value, errorTrace.value ?? undefined)
      }
    }
  }

  // ── 页面可见性：隐藏即暂停，恢复补拉并重排定时器 ──
  function bindVisibility(): void {
    if (listenerAttached || typeof document === 'undefined') return
    isPaused = document.hidden
    document.addEventListener('visibilitychange', handleVisibilityChange)
    listenerAttached = true
  }

  function unbindVisibility(): void {
    if (!listenerAttached || typeof document === 'undefined') return
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    listenerAttached = false
  }

  function handleVisibilityChange(): void {
    if (typeof document === 'undefined') return
    if (document.hidden) {
      isPaused = true
      clearTimer()
      return
    }
    isPaused = false
    if (!isPolling.value || isStopped.value) return
    void pollNow().then(() => {
      if (isPolling.value && !isStopped.value) scheduleNext(currentInterval)
    })
  }

  async function start(): Promise<void> {
    if (isPolling.value) return
    isPolling.value = true
    isStopped.value = false
    currentInterval = baseInterval
    consecutiveFailures = 0
    error.value = null
    errorTrace.value = null
    bindVisibility()
    // 启动即 poll 一次，让调用方状态即时同步
    await tick()
    if (!isStopped.value && !isPaused) scheduleNext(currentInterval)
  }

  function stop(): void {
    finish()
  }

  async function pollNow(): Promise<void> {
    if (isStopped.value) return
    await tick()
  }

  onScopeDispose(() => {
    stop()
  })

  return { isPolling, isStopped, lastResult, error, errorTrace, start, stop, pollNow }
}
