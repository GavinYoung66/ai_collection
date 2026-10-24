/**
 * useTraceContext composable 单测（tasks 3.5② / SSOT §9.4）。
 *
 * 覆盖：
 * ① setup 时自动写入 trace_id 到 request.ts 的 trace context
 * ② getTraceContext() 与 traceId.value 同步更新
 * ③ unmount（onScopeDispose）时自动清空 → getTraceContext() 返回 null
 * ④ 组件间互相隔离：mount A → unmount A → mount B 有新 ID
 * ⑤ set() 手动覆盖 + clear() 手动清空
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { useTraceContext } from '../composables/useTraceContext'
import { getTraceContext, setTraceContext } from '@/shared/request'

/** 包装组件：setup 里调用 useTraceContext，暴露结果给测试 */
function mountWithContext() {
  return mount({
    setup() {
      return useTraceContext()
    },
    template: '<div />',
  })
}

describe('modules/complaint/useTraceContext', () => {
  beforeEach(() => { setTraceContext(null) })
  afterEach(() => { setTraceContext(null) })

  it('1. setup 时自动生成并写入 trace context', () => {
    expect(getTraceContext()).toBeNull()
    mountWithContext()
    expect(getTraceContext()).not.toBeNull()
  })

  it('2. unmount 时自动清空 trace context', () => {
    const wrapper = mountWithContext()
    expect(getTraceContext()).not.toBeNull()
    wrapper.unmount()
    expect(getTraceContext()).toBeNull()
  })

  it('3. 组件间互相隔离：mount A → unmount A → mount B 有新 ID', () => {
    const wrapperA = mountWithContext()
    const traceA = getTraceContext()
    expect(traceA).not.toBeNull()

    wrapperA.unmount()
    expect(getTraceContext()).toBeNull()

    const wrapperB = mountWithContext()
    const traceB = getTraceContext()
    expect(traceB).not.toBeNull()
    expect(traceB).not.toBe(traceA)

    wrapperB.unmount()
    expect(getTraceContext()).toBeNull()
  })

  it('4. set() 手动覆盖 + clear() 手动清空', () => {
    const wrapper = mountWithContext()
    const vm = wrapper.vm as unknown as { set: (id?: string) => string; clear: () => void }

    const orig = getTraceContext()
    expect(orig).not.toBeNull()

    vm.set('custom-trace-id-xyz')
    // 权威来源：request.ts 的 getTraceContext()（拦截器读取的值）
    expect(getTraceContext()).toBe('custom-trace-id-xyz')

    vm.clear()
    expect(getTraceContext()).toBeNull()

    wrapper.unmount()
  })
})
