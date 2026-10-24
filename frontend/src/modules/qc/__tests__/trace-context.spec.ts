/**
 * useQcTraceContext 单测（f2-qc-console 批次4 / tasks 5.5，design D5）。
 *
 * mock shared/request 验证桥接契约：
 * ① 挂载（setup）时 setTraceContext 写入非空 trace_id（拦截器据此注入同一 X-Trace-Id）
 * ② 卸载（onScopeDispose）时 setTraceContext(null) 清空 → 离开后列表请求不携带
 * ③ 组件间隔离：mount A → unmount A → mount B 得到新 ID
 * ④ set()/clear() 手动覆盖与清空
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// ── mock shared/request：带状态的最小实现，验证写入/清空时序 ──
const reqMocks = vi.hoisted(() => {
  let ctx: string | null = null
  return {
    setTraceContext: vi.fn((v: string | null) => { ctx = v }),
    getTraceContext: vi.fn(() => ctx),
  }
})
vi.mock('@/shared/request', () => ({
  setTraceContext: reqMocks.setTraceContext,
  getTraceContext: reqMocks.getTraceContext,
}))

import { useQcTraceContext } from '../composables/useQcTraceContext'

/** 包装组件：setup 里调用 useQcTraceContext，暴露结果给测试 */
function mountWithContext() {
  return mount({
    setup() {
      return useQcTraceContext()
    },
    template: '<div />',
  })
}

describe('modules/qc/useQcTraceContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    reqMocks.setTraceContext(null)
    vi.clearAllMocks()
  })

  it('1. 挂载时写入 trace context（同一挂载周期内请求共用同一 X-Trace-Id）', () => {
    expect(reqMocks.getTraceContext()).toBeNull()
    mountWithContext()
    expect(reqMocks.setTraceContext).toHaveBeenCalledWith(expect.any(String))
    const written = reqMocks.setTraceContext.mock.calls[0][0] as string
    expect(written.length).toBeGreaterThan(0)
    expect(reqMocks.getTraceContext()).toBe(written)
  })

  it('2. 卸载时清空 trace context（离开后请求不携带 X-Trace-Id）', () => {
    const wrapper = mountWithContext()
    expect(reqMocks.getTraceContext()).not.toBeNull()
    wrapper.unmount()
    expect(reqMocks.setTraceContext).toHaveBeenLastCalledWith(null)
    expect(reqMocks.getTraceContext()).toBeNull()
  })

  it('3. 组件间隔离：mount A → unmount A → mount B 有新 ID', () => {
    const wrapperA = mountWithContext()
    const traceA = reqMocks.getTraceContext()
    expect(traceA).not.toBeNull()

    wrapperA.unmount()
    expect(reqMocks.getTraceContext()).toBeNull()

    const wrapperB = mountWithContext()
    const traceB = reqMocks.getTraceContext()
    expect(traceB).not.toBeNull()
    expect(traceB).not.toBe(traceA)

    wrapperB.unmount()
    expect(reqMocks.getTraceContext()).toBeNull()
  })

  it('4. set() 手动覆盖 + clear() 手动清空', () => {
    const wrapper = mountWithContext()
    const vm = wrapper.vm as unknown as { set: (id?: string) => string; clear: () => void }

    vm.set('qc-trace-xyz')
    expect(reqMocks.setTraceContext).toHaveBeenLastCalledWith('qc-trace-xyz')
    expect(reqMocks.getTraceContext()).toBe('qc-trace-xyz')

    vm.clear()
    expect(reqMocks.setTraceContext).toHaveBeenLastCalledWith(null)
    expect(reqMocks.getTraceContext()).toBeNull()

    wrapper.unmount()
  })
})
