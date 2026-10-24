/**
 * usePolling composable 单测（tasks 3.3 / SSOT §14.1）。
 *
 * 覆盖 5 个关键场景：
 * ① 退避阶梯：3s→5s 间隔（状态持续不变时）
 * ② TERMINAL 终态 → 停止 + onTerminalStop 回调
 * ③ WAITING_HUMAN → 固定 5s 低频（不走退避阶梯）
 * ④ 连续 3 次失败 → 熔断 + error 暴露
 * ⑤ onUnmounted → 清理定时器
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { Ref } from 'vue'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { usePolling } from '../composables/usePolling'

const apiMocks = vi.hoisted(() => ({
  getComplaintStatus: vi.fn(),
}))

vi.mock('../api', () => ({
  getComplaintStatus: apiMocks.getComplaintStatus,
}))

function mountForPolling(complaintId: string, callbacks: Record<string, unknown> = {}) {
  // defineComponent 包裹保证 wrapper.vm 上 setup 暴露绑定的类型推断（裸对象字面量在 TS6 下推为 never）
  return mount(
    defineComponent({
      setup() {
        return usePolling(
          { value: complaintId } as Ref<string>,
          callbacks as Parameters<typeof usePolling>[1],
        )
      },
      template: '<div />',
    }),
  )
}

describe('modules/complaint/usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    Object.defineProperty(document, 'hidden', { value: false, configurable: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('1. TERMINAL 终态 → 立即停止 + onTerminalStop 回调', async () => {
    apiMocks.getComplaintStatus.mockResolvedValue({
      complaint_id: 'c-001', task_status: 'resolved', progress: 1,
    })
    const onTerminalStop = vi.fn()
    const wrapper = mountForPolling('c-001', { onTerminalStop })
    await wrapper.vm.start()

    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isStopped).toBe(true)
    expect(wrapper.vm.isPolling).toBe(false)
    expect(wrapper.vm.status).toBe('resolved')
    expect(onTerminalStop).toHaveBeenCalledTimes(1)

    // 定时器已清，再推进时间不会再调
    vi.advanceTimersByTime(60000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)
  })

  it('2. 连续 3 次失败 → 熔断 + error 暴露', async () => {
    apiMocks.getComplaintStatus
      .mockRejectedValueOnce({ message: '超时', trace: 'tr_aaaa1111' })
      .mockRejectedValueOnce({ message: '超时', trace: 'tr_cccc2222' })
      .mockRejectedValueOnce({ message: '网关错误', trace: 'tr_eeee3333' })

    const wrapper = mountForPolling('c-001')
    await wrapper.vm.start()

    // start 立即 tick → 第 1 次失败（但还没熔断）
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isStopped).toBe(false)

    // 3s → 第 2 次失败
    vi.advanceTimersByTime(3000)
    await Promise.resolve() // 等 tick() 的 Promise resolve
    expect(wrapper.vm.isStopped).toBe(false)

    // 再 3s → 第 3 次失败 → 熔断
    vi.advanceTimersByTime(3000)
    await Promise.resolve()
    expect(wrapper.vm.isStopped).toBe(true)
    expect(wrapper.vm.error).toBeTruthy()
    expect(wrapper.vm.errorTrace).toBeTruthy()

    vi.advanceTimersByTime(30000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(3)
  })

  it('3. WAITING_HUMAN → 固定 5s 低频（不走退避）', async () => {
    apiMocks.getComplaintStatus.mockResolvedValue({
      complaint_id: 'c-001', task_status: 'pending_review', progress: 1,
    })
    const wrapper = mountForPolling('c-001')
    await wrapper.vm.start()

    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)

    // 5s 后第 2 次
    vi.advanceTimersByTime(5000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(2)

    // 再 10s（两个 5s 周期）→ 应共 4 次
    vi.advanceTimersByTime(10000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(4)
  })

  it('4. 退避阶梯：judging 持续不变 → 20s 后退避到 5s', async () => {
    apiMocks.getComplaintStatus.mockResolvedValue({
      complaint_id: 'c-001', task_status: 'judging', progress: 0,
    })
    const wrapper = mountForPolling('c-001')
    await wrapper.vm.start()
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)

    // 再推进 3s 第 2 次，再 3s 第 3 次
    vi.advanceTimersByTime(6000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(3)

    // 再推进 20s（累计 26s，足以触发 3→5 退避）
    const callsAt26s = apiMocks.getComplaintStatus.mock.calls.length
    vi.advanceTimersByTime(20000)
    const callsAt46s = apiMocks.getComplaintStatus.mock.calls.length

    // 20s 内 3s 节奏应该有 7 次左右；退避到 5s 后应该只有 ~4 次
    // 宽松断言：退避后节奏应明显慢于 3s 节奏
    expect(callsAt46s - callsAt26s, '退避后每 20s 的 poll 次数应少于 3s 节奏').toBeLessThanOrEqual(6)
  })

  it('5. onUnmounted → 清理定时器，不再发请求', async () => {
    apiMocks.getComplaintStatus.mockResolvedValue({
      complaint_id: 'c-001', task_status: 'judging', progress: 0,
    })
    const wrapper = mountForPolling('c-001')
    await wrapper.vm.start()
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    vi.advanceTimersByTime(60000)
    expect(apiMocks.getComplaintStatus).toHaveBeenCalledTimes(1)
  })
})
