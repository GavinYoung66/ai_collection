/**
 * 轮询 composable（tasks 3.3 / SSOT §14.1 / design.md D2）。
 *
 * 契约：
 * - 只轮询 api.getComplaintStatus，**禁打详情端点**
 * - 首轮 3s；连续 20s 状态无变化退避 5s；再连续 20s 无变化退避 10s（上限 10s）
 * - WAITING_HUMAN 态独立代码路径：固定 5s 低频，不走退避阶梯
 * - TERMINAL 态立即停止 + 调回调（详情页负责补拉 verdict/agent-logs）
 * - document.hidden 暂停，visibilitychange 恢复后立即补一次（不等定时器）
 * - 连续 3 次失败熔断 + 暴露 error/errorTrace 供 UI 展示
 * - onUnmounted 清理定时器 + visibilitychange listener
 *
 * 设计原则（D2）：judge 长请求的成败**不控制**轮询生命周期；
 * 轮询只由 task_status 驱动。详情页传入回调，本 composable 不内硬编码补拉逻辑。
 */
import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { getComplaintStatus } from '../api'
import type { TaskStatus } from '../types'
import { TERMINAL_TASK_STATUS, WAITING_HUMAN_TASK_STATUS } from '../types'

export interface UsePollingCallbacks {
  /** 到达终态时调用（详情页负责补拉 verdict/agent-logs + 刷新 UI） */
  onTerminalStop?: () => void
  /** 进入等待人工态时调用（UI 提示） */
  onWaitingHuman?: () => void
  /** 熔断/停止时调用，errMessage + trace 供 UI 展示 */
  onError?: (message: string, trace?: string) => void
}

export interface UsePollingResult {
  isPolling: Ref<boolean>
  isStopped: Ref<boolean>
  status: Ref<TaskStatus | null>
  error: Ref<string | null>
  errorTrace: Ref<string | null>
  start: () => void
  stop: () => void
  /** 外部调用：强制立即 poll 一次（如操作后需要刷新） */
  pollNow: () => Promise<void>
}

export function usePolling(
  complaintId: Ref<string>,
  callbacks: UsePollingCallbacks = {},
): UsePollingResult {
  // ── 对外状态 ──
  const isPolling = ref(false)
  const isStopped = ref(false)
  const status = ref<TaskStatus | null>(null)
  const error = ref<string | null>(null)
  const errorTrace = ref<string | null>(null)

  // ── 内部状态 ──
  let timerId: ReturnType<typeof setInterval> | null = null
  let currentInterval = 3000 // 3s 首轮
  let lastStatusValue: TaskStatus | null = null
  let lastChangedAt = 0 // ms timestamp
  let consecutiveFailures = 0
  let isPaused = false // document.hidden 暂停中
  let listenerAttached = false

  // ── 工具：清定时器 ──
  function clearTimer(): void {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
  }

  // ── 工具：重设定时器（interval 在 poll 结束后动态计算） ──
  function scheduleNext(intervalMs: number): void {
    clearTimer()
    currentInterval = intervalMs
    timerId = setInterval(tick, intervalMs)
  }

  // ── 核心：单次 poll ──
  async function tick(): Promise<void> {
    if (!complaintId.value || isPaused || isStopped.value) return
    try {
      const res = await getComplaintStatus(complaintId.value)
      consecutiveFailures = 0
      error.value = null
      errorTrace.value = null
      status.value = res.task_status

      // 终态 → 停止
      if ((TERMINAL_TASK_STATUS as readonly string[]).includes(res.task_status)) {
        isPolling.value = false
        isStopped.value = true
        clearTimer()
        callbacks.onTerminalStop?.()
        return
      }

      // 等待人工态 → 固定 5s 低频
      if ((WAITING_HUMAN_TASK_STATUS as readonly string[]).includes(res.task_status)) {
        const nextInterval = 5000
        if (currentInterval !== nextInterval) {
          scheduleNext(nextInterval)
        }
        // 首次进入时提示一次
        if (lastStatusValue !== res.task_status) {
          callbacks.onWaitingHuman?.()
        }
      } else {
        // 正常态：退避阶梯
        const now = Date.now()
        if (lastStatusValue !== res.task_status) {
          // 状态变化 → 重置到 3s
          lastChangedAt = now
          if (currentInterval !== 3000) {
            scheduleNext(3000)
          }
        } else {
          // 状态未变，检查退避
          const elapsed = now - lastChangedAt
          let nextInterval = currentInterval
          if (elapsed >= 20_000) {
            // 20s 无变化 → 3s→5s→10s 阶梯
            if (currentInterval === 3000) {
              nextInterval = 5000
            } else if (currentInterval === 5000) {
              nextInterval = 10000
            }
            // 10000 是上限，不再升
            if (nextInterval !== currentInterval) {
              scheduleNext(nextInterval)
            }
          }
        }
      }

      lastStatusValue = res.task_status
    } catch (err: unknown) {
      const e = err as { message?: string; trace?: string }
      consecutiveFailures++
      if (consecutiveFailures >= 3) {
        // 熔断
        isPolling.value = false
        isStopped.value = true
        error.value = e?.message ?? '轮询失败'
        errorTrace.value = e?.trace ?? null
        clearTimer()
        callbacks.onError?.(error.value, errorTrace.value ?? undefined)
      }
    }
  }

  // ── 工具：立即同步 document.hidden 并绑定 visibilitychange ──
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
      // 恢复后立即补一次（不等定时器）
      void pollNow()
    }
  }

  // ── start / stop ──
  async function start(): Promise<void> {
    if (!complaintId.value || isStopped.value) return
    if (isPolling.value) return // 已在运行
    isPolling.value = true
    isStopped.value = false
    lastStatusValue = null
    lastChangedAt = Date.now()
    consecutiveFailures = 0
    error.value = null
    errorTrace.value = null
    bindVisibility()
    // 启动后先立即 poll 一次（await 等待结果，让状态同步到调用方）
    await tick()
    if (!isStopped.value) {
      // 若 tick 没触发停止，则安排后续定时器
      scheduleNext(currentInterval)
    }
  }

  function stop(): void {
    isPolling.value = false
    isStopped.value = true
    clearTimer()
    unbindVisibility()
  }

  async function pollNow(): Promise<void> {
    if (!complaintId.value || isStopped.value) return
    await tick()
  }

  // ── 外部 complaintId 变化 → 重启 ──
  watch(complaintId, (newId, oldId) => {
    if (newId === oldId) return
    stop()
    if (newId) start()
  })

  // ── 自动 onUnmounted 清理（Composition API setup 上下文可用时） ──
  onScopeDispose(() => {
    stop()
  })

  return { isPolling, isStopped, status, error, errorTrace, start, stop, pollNow }
}
