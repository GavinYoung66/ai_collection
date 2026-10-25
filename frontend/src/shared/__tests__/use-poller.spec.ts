/**
 * usePoller 单测（SSOT §19.5）。
 * 覆盖：终态自停、按基准间隔轮询、注入式退避阶梯、失败熔断、隐藏暂停/恢复重排、
 * 卸载清理、stop 后可重启。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { usePoller, type UsePollerOptions, type UsePollerResult } from '@/shared/composables/usePoller'

function mountPoller<T>(
  options: UsePollerOptions<T>,
): { poller: UsePollerResult<T>; unmount: () => void } {
  let poller!: UsePollerResult<T>
  const wrapper = mount(
    defineComponent({
      setup() {
        poller = usePoller(options)
        return {}
      },
      template: '<div />',
    }),
  )
  return { poller, unmount: () => wrapper.unmount() }
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true })
}

describe('shared/usePoller', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setHidden(false)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('1. 终态 → 立即停止并回调，不再产生定时请求', async () => {
    const poll = vi.fn().mockResolvedValue({ done: true })
    const onTerminal = vi.fn()
    const { poller } = mountPoller({
      poll,
      isTerminal: (r: { done: boolean }) => r.done,
      onTerminal,
    })

    await poller.start()
    expect(poll).toHaveBeenCalledTimes(1)
    expect(poller.isStopped.value).toBe(true)
    expect(poller.isPolling.value).toBe(false)
    expect(poller.lastResult.value).toEqual({ done: true })
    expect(onTerminal).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(60000)
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it('2. 非终态 → 按基准间隔持续轮询', async () => {
    const poll = vi.fn().mockResolvedValue({ done: false })
    const { poller } = mountPoller({
      poll,
      isTerminal: (r: { done: boolean }) => r.done,
      intervalMs: 3000,
    })

    await poller.start()
    expect(poll).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(9000)
    await vi.advanceTimersByTimeAsync(0)
    expect(poll).toHaveBeenCalledTimes(4)
  })

  it('3. 注入式退避：nextInterval 返回更大间隔后节奏放慢', async () => {
    // 模拟「第 2 次起改为 10s」的策略，验证引擎按注入值重排
    let calls = 0
    const poll = vi.fn().mockResolvedValue({ n: 0 })
    const { poller } = mountPoller({
      poll,
      isTerminal: () => false,
      intervalMs: 3000,
      nextInterval: (_r, current) => {
        calls++
        return calls >= 2 ? 10000 : current
      },
    })

    await poller.start()
    vi.advanceTimersByTime(3000) // 第 2 次 tick 后切到 10s
    await vi.advanceTimersByTimeAsync(0)
    expect(poll).toHaveBeenCalledTimes(2)

    vi.advanceTimersByTime(9000) // 3+9=12s：若仍是 3s 会有更多次
    await vi.advanceTimersByTimeAsync(0)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('4. 连续失败达到 maxFailures → 熔断并暴露 error/errorTrace', async () => {
    const poll = vi
      .fn()
      .mockRejectedValue({ message: '网关错误', trace: 'tr_12345678' })
    const onError = vi.fn()
    const { poller } = mountPoller({
      poll,
      isTerminal: () => false,
      intervalMs: 3000,
      maxFailures: 2,
      onError,
    })

    await poller.start() // 第 1 次失败
    expect(poller.isStopped.value).toBe(false)

    vi.advanceTimersByTime(3000)
    await vi.advanceTimersByTimeAsync(0) // 第 2 次失败 → 熔断
    expect(poller.isStopped.value).toBe(true)
    expect(poller.error.value).toBe('网关错误')
    expect(poller.errorTrace.value).toBe('tr_12345678')
    expect(onError).toHaveBeenCalledWith('网关错误', 'tr_12345678')

    vi.advanceTimersByTime(30000)
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('5. 隐藏暂停不再请求；恢复可见后补一次并重排定时器', async () => {
    const poll = vi.fn().mockResolvedValue({ done: false })
    const { poller } = mountPoller({
      poll,
      isTerminal: (r: { done: boolean }) => r.done,
      intervalMs: 3000,
    })

    await poller.start()
    expect(poll).toHaveBeenCalledTimes(1)

    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    vi.advanceTimersByTime(30000)
    expect(poll, '隐藏期间不应发请求').toHaveBeenCalledTimes(1)

    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(poll, '恢复可见应立即补一次').toHaveBeenCalledTimes(2)

    // 重排生效：之后仍按 3s 继续轮询（否则轮询会静默死亡）
    vi.advanceTimersByTime(6000)
    await vi.advanceTimersByTimeAsync(0)
    expect(poll.mock.calls.length, '恢复后定时器必须重排').toBeGreaterThanOrEqual(3)
  })

  it('6. 卸载 → 清理定时器', async () => {
    const poll = vi.fn().mockResolvedValue({ done: false })
    const { poller, unmount } = mountPoller({
      poll,
      isTerminal: (r: { done: boolean }) => r.done,
      intervalMs: 3000,
    })

    await poller.start()
    expect(poll).toHaveBeenCalledTimes(1)
    unmount()
    vi.advanceTimersByTime(60000)
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it('7. stop 之后允许 start 重启（页面重新激活/任务重新在途）', async () => {
    const poll = vi.fn().mockResolvedValue({ done: false })
    const { poller } = mountPoller({
      poll,
      isTerminal: (r: { done: boolean }) => r.done,
      intervalMs: 3000,
    })

    await poller.start()
    poller.stop()
    expect(poller.isStopped.value).toBe(true)

    await poller.start()
    expect(poller.isStopped.value).toBe(false)
    expect(poller.isPolling.value).toBe(true)
    vi.advanceTimersByTime(3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(poll.mock.calls.length).toBeGreaterThanOrEqual(3)
  })
})
