/**
 * kb 入库轮询 composable（f2-rag-console 批次④ / tasks 4.6，design D2）。
 *
 * 参照 complaint/composables/usePolling.ts:42 的模式实现，但入参为「轮询函数 +
 * 终态谓词」，不硬编码 doc_id/status 等业务字段（D2：通用化下沉留待第三个使用者，
 * 两份实现并存期间注释互相引用来源，防漂移）：
 * - 固定 3s 间隔；start 后立即 poll 一次，让调用方状态即时同步；
 * - 终态自停：isTerminal(result) 为 true → 停止并回调 onTerminal（携最后一次快照）；
 * - 连续失败熔断：默认 5 次 → 停止并回调 onError（spec「上传必须为异步入库并轮询
 *   至终态」）；
 * - document.hidden 暂停并清定时器，visibilitychange 恢复后立即补一次并重排定时器；
 * - onScopeDispose 清理定时器与监听器，组件卸载无残留（spec「组件卸载后无残留定时器」）。
 *
 * 与 complaint 侧差异：complaint 在 hidden 清理后恢复时只补一次不重排定时器（其
 * 详情页由回调补拉后续），本实现在恢复时显式 reschedule，保证轮询续航。
 */
import { onScopeDispose, ref, type Ref } from 'vue'

export interface UseKbIngestPollingOptions<T> {
  /** 单次轮询：返回一次快照（如目标文档详情）；抛错计入连续失败 */
  poll: () => Promise<T>
  /** 终态谓词：返回 true 时停止轮询 */
  isTerminal: (result: T) => boolean
  /** 轮询间隔 ms，默认 3000（对齐 complaint 首轮 3s） */
  intervalMs?: number
  /** 连续失败熔断次数，默认 5 */
  maxFailures?: number
  /** 到达终态回调（参数为最后一次快照） */
  onTerminal?: (result: T) => void
  /** 熔断回调（message 取拦截器已加工文案） */
  onError?: (message: string) => void
}

export interface UseKbIngestPollingResult {
  isPolling: Ref<boolean>
  isStopped: Ref<boolean>
  /** 熔断时的错误文案（终态自停不产生 error） */
  error: Ref<string | null>
  start: () => Promise<void>
  stop: () => void
  /** 外部调用：立即 poll 一次（如页面恢复可见时） */
  pollNow: () => Promise<void>
}

export function useKbIngestPolling<T>(
  options: UseKbIngestPollingOptions<T>,
): UseKbIngestPollingResult {
  const intervalMs = options.intervalMs ?? 3000
  const maxFailures = options.maxFailures ?? 5

  // ── 对外状态 ──
  const isPolling = ref(false)
  const isStopped = ref(false)
  const error = ref<string | null>(null)

  // ── 内部状态 ──
  let timerId: ReturnType<typeof setInterval> | null = null
  let consecutiveFailures = 0
  let isPaused = false // document.hidden 暂停中
  let listenerAttached = false

  function clearTimer(): void {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
  }

  function scheduleNext(): void {
    clearTimer()
    timerId = setInterval(tick, intervalMs)
  }

  /** 统一停止收口：终态自停 / 熔断 / 外部 stop 共用 */
  function finish(): void {
    isPolling.value = false
    isStopped.value = true
    clearTimer()
    unbindVisibility()
  }

  // ── 核心：单次 poll ──
  async function tick(): Promise<void> {
    if (isPaused || isStopped.value) return
    try {
      const result = await options.poll()
      consecutiveFailures = 0
      error.value = null
      if (options.isTerminal(result)) {
        finish()
        options.onTerminal?.(result)
      }
    } catch (err: unknown) {
      consecutiveFailures++
      if (consecutiveFailures >= maxFailures) {
        const message = (err as { message?: string })?.message ?? '轮询失败'
        error.value = message
        finish()
        options.onError?.(message)
      }
    }
  }

  // ── document.hidden 暂停 / 恢复 ──
  function bindVisibility(): void {
    if (listenerAttached) return
    if (typeof document !== 'undefined') {
      isPaused = document.hidden
      document.addEventListener('visibilitychange', handleVisibilityChange)
      listenerAttached = true
    }
  }

  function unbindVisibility(): void {
    if (!listenerAttached) return
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
    listenerAttached = false
  }

  function handleVisibilityChange(): void {
    if (typeof document === 'undefined') return
    if (document.hidden) {
      isPaused = true
      clearTimer()
    } else {
      isPaused = false
      if (!isPolling.value || isStopped.value) return
      // 恢复后立即补一次（不等定时器）并重排定时器，保证续航
      void pollNow().then(() => {
        if (isPolling.value && !isStopped.value) scheduleNext()
      })
    }
  }

  // ── start / stop / pollNow ──
  async function start(): Promise<void> {
    if (isPolling.value) return // 已在运行
    isPolling.value = true
    isStopped.value = false
    consecutiveFailures = 0
    error.value = null
    bindVisibility()
    // 启动后先立即 poll 一次（await 让状态同步到调用方）
    await tick()
    if (!isStopped.value) {
      scheduleNext()
    }
  }

  function stop(): void {
    finish()
  }

  async function pollNow(): Promise<void> {
    if (isStopped.value) return
    await tick()
  }

  // ── 组件卸载清理（spec「组件卸载后无残留定时器」）──
  onScopeDispose(() => {
    stop()
  })

  return { isPolling, isStopped, error, start, stop, pollNow }
}
