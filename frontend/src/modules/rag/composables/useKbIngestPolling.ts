/**
 * kb 入库轮询适配器（f2-rag-console 批次④ / tasks 4.6 / SSOT §19.5，design D2）。
 *
 * 调度能力已下沉到 `shared/composables/usePoller`；本文件只做参数转译与返回面裁剪：
 * - 固定 3s 间隔；start 后立即 poll 一次，让调用方状态即时同步；
 * - 终态自停：isTerminal(result) 为 true → 停止并回调 onTerminal（携最后一次快照）；
 * - 连续失败熔断：默认 5 次 → 停止并回调 onError（spec「上传必须为异步入库并轮询
 *   至终态」）；
 * - document.hidden 暂停并清定时器，visibilitychange 恢复后立即补一次并重排定时器；
 * - onScopeDispose 清理定时器与监听器，组件卸载无残留（spec「组件卸载后无残留定时器」）。
 */
import { type Ref } from 'vue'
import { usePoller } from '@/shared'

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
  const poller = usePoller<T>({
    poll: options.poll,
    isTerminal: options.isTerminal,
    intervalMs: options.intervalMs ?? 3000,
    maxFailures: options.maxFailures ?? 5,
    onTerminal: options.onTerminal,
    onError: (message) => options.onError?.(message),
  })

  return {
    isPolling: poller.isPolling,
    isStopped: poller.isStopped,
    error: poller.error,
    start: poller.start,
    stop: poller.stop,
    pollNow: poller.pollNow,
  }
}
