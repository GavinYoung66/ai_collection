/**
 * useKbIngestPolling composable 单测（f2-rag-console 批次⑥ / tasks 8.3 轮询部分）。
 *
 * 照 modules/complaint/__tests__/polling.spec.ts 先例（vi.useFakeTimers +
 * defineComponent 包裹挂载），覆盖 spec「上传必须为异步入库并轮询至终态」：
 * ① start 后立即 poll 一次，终态（active/failed）自停 + onTerminal 回调；
 * ② 连续失败熔断（默认 5 次）→ 停止 + error 暴露；
 * ③ unmount（onScopeDispose）清理定时器，无后台请求继续发出。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useKbIngestPolling } from '../composables/useKbIngestPolling'

interface MockDoc {
  status: 'processing' | 'active' | 'failed'
}

function mountPolling(options: {
  poll: () => Promise<MockDoc>
  onTerminal?: (r: MockDoc) => void
  onError?: (message: string) => void
}) {
  // defineComponent 包裹保证 wrapper.vm 上 setup 暴露绑定的类型推断（裸对象字面量推为 never）
  return mount(
    defineComponent({
      setup() {
        return useKbIngestPolling<MockDoc>({
          poll: options.poll,
          isTerminal: (doc) => doc.status === 'active' || doc.status === 'failed',
          onTerminal: options.onTerminal,
          onError: options.onError,
        })
      },
      template: '<div />',
    }),
  )
}

describe('modules/rag/useKbIngestPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'hidden', { value: false, configurable: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('1. start 立即 poll 一次；终态 active → 自停 + onTerminal，之后不再发请求', async () => {
    const poll = vi.fn().mockResolvedValue({ status: 'active' } satisfies MockDoc)
    const onTerminal = vi.fn()
    const wrapper = mountPolling({ poll, onTerminal })
    await wrapper.vm.start()

    expect(poll).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isStopped).toBe(true)
    expect(wrapper.vm.isPolling).toBe(false)
    expect(onTerminal).toHaveBeenCalledTimes(1)
    expect(onTerminal.mock.calls[0][0]).toEqual({ status: 'active' })

    // 定时器已清，推进时间不会再调
    vi.advanceTimersByTime(60000)
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it('2. processing 非终态 → 按 3s 间隔续轮，直到 failed 终态自停', async () => {
    const poll = vi
      .fn()
      .mockResolvedValueOnce({ status: 'processing' } satisfies MockDoc)
      .mockResolvedValueOnce({ status: 'processing' } satisfies MockDoc)
      .mockResolvedValue({ status: 'failed' } satisfies MockDoc)
    const onTerminal = vi.fn()
    const wrapper = mountPolling({ poll, onTerminal })
    await wrapper.vm.start()
    expect(poll).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isStopped).toBe(false)

    vi.advanceTimersByTime(3000)
    await Promise.resolve()
    expect(poll).toHaveBeenCalledTimes(2)

    vi.advanceTimersByTime(3000)
    await Promise.resolve()
    await Promise.resolve()
    expect(poll).toHaveBeenCalledTimes(3)
    expect(wrapper.vm.isStopped).toBe(true)
    expect(onTerminal).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(30000)
    expect(poll).toHaveBeenCalledTimes(3)
  })

  it('3. 连续 5 次失败 → 熔断停止 + error 暴露 + onError 回调', async () => {
    const poll = vi.fn().mockRejectedValue(new Error('网络异常，请稍后重试（trace: 无）'))
    const onError = vi.fn()
    const wrapper = mountPolling({ poll, onError })
    await wrapper.vm.start()
    expect(poll).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isStopped).toBe(false)

    for (let i = 0; i < 4; i++) {
      vi.advanceTimersByTime(3000)
      await Promise.resolve()
      await Promise.resolve()
    }
    expect(poll).toHaveBeenCalledTimes(5)
    expect(wrapper.vm.isStopped).toBe(true)
    expect(wrapper.vm.error).toBeTruthy()
    expect(onError).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(30000)
    expect(poll).toHaveBeenCalledTimes(5)
  })

  it('4. unmount → onScopeDispose 清理定时器，不再发请求', async () => {
    const poll = vi.fn().mockResolvedValue({ status: 'processing' } satisfies MockDoc)
    const wrapper = mountPolling({ poll })
    await wrapper.vm.start()
    expect(poll).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    vi.advanceTimersByTime(60000)
    expect(poll).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isPolling).toBe(false)
  })
})
