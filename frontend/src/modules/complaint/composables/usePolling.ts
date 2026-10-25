/**
 * 轮询适配器（tasks 3.3 / SSOT §14.1 / §19.5 / design.md D2）。
 *
 * 调度能力已下沉到 `shared/composables/usePoller`；本文件只保留业务策略：
 * - 只轮询 api.getComplaintStatus，**禁打详情端点**
 * - 首轮 3s；连续 20s 状态无变化退避 5s；再连续 20s 无变化退避 10s（上限 10s）
 * - WAITING_HUMAN 态独立代码路径：固定 5s 低频，不走退避阶梯
 * - COMPLAINT_TASK_STATUS 终态 → 停止 + 回调（详情页负责补拉 verdict/agent-logs）
 * - 连续 3 次失败熔断 + 暴露 error/errorTrace 供 UI 展示
 *
 * 设计原则（D2）：judge 长请求的成败**不控制**轮询生命周期；
 * 轮询只由 task_status 驱动。详情页传入回调，本 composable 不内硬编码补拉逻辑。
 */
import { computed, watch, type Ref } from 'vue'
import { usePoller } from '@/shared'
import { getComplaintStatus } from '../api'
import type { TaskStatus, StatusResponse } from '../types'
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

const FIRST_INTERVAL = 3000
const WAITING_HUMAN_INTERVAL = 5000
const BACKOFF_STEP = 20_000

export function usePolling(
  complaintId: Ref<string>,
  callbacks: UsePollingCallbacks = {},
): UsePollingResult {
  // 退避策略状态（业务语义，留在适配器；引擎不感知 task_status）
  let lastStatusValue: TaskStatus | null = null
  let lastChangedAt = 0

  const poller = usePoller<StatusResponse>({
    poll: () => getComplaintStatus(complaintId.value),
    isTerminal: (res) => (TERMINAL_TASK_STATUS as readonly string[]).includes(res.task_status),
    intervalMs: FIRST_INTERVAL,
    maxFailures: 3,
    onTerminal: () => callbacks.onTerminalStop?.(),
    onError: (message, trace) => callbacks.onError?.(message, trace),
    nextInterval: (res, current) => {
      // 等待人工：固定低频，不走退避
      if ((WAITING_HUMAN_TASK_STATUS as readonly string[]).includes(res.task_status)) {
        if (lastStatusValue !== res.task_status) callbacks.onWaitingHuman?.()
        lastStatusValue = res.task_status
        return WAITING_HUMAN_INTERVAL
      }

      const now = Date.now()
      // 状态变化 → 重置到首轮间隔
      if (lastStatusValue !== res.task_status) {
        lastStatusValue = res.task_status
        lastChangedAt = now
        return FIRST_INTERVAL
      }

      // 状态未变：每连续 20s 升一档 3s→5s→10s（10s 封顶）
      let next = current
      if (now - lastChangedAt >= BACKOFF_STEP) {
        if (current === FIRST_INTERVAL) next = WAITING_HUMAN_INTERVAL
        else if (current === WAITING_HUMAN_INTERVAL) next = 10_000
        // 升档后重置基准：保证「再连续 20s 无变化」才继续升档
        if (next !== current) lastChangedAt = now
      }
      return next
    },
  })

  const status = computed<TaskStatus | null>(() => poller.lastResult.value?.task_status ?? null)

  async function start(): Promise<void> {
    if (!complaintId.value) return
    // 每次启动视为新一轮：退避状态归零
    lastStatusValue = null
    lastChangedAt = Date.now()
    await poller.start()
  }

  async function pollNow(): Promise<void> {
    if (!complaintId.value) return
    await poller.pollNow()
  }

  // ── 外部 complaintId 变化 → 重启（poller 支持 stop 后再次 start）──
  watch(complaintId, (newId, oldId) => {
    if (newId === oldId) return
    poller.stop()
    if (newId) void start()
  })

  return {
    isPolling: poller.isPolling,
    isStopped: poller.isStopped,
    status,
    error: poller.error,
    errorTrace: poller.errorTrace,
    start,
    stop: poller.stop,
    pollNow,
  }
}
