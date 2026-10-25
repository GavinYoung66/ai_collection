/**
 * useAsyncTask 单测（SSOT §19.4）。
 * 覆盖：挂载即执行、三态切换、错误归一、过期响应丢弃、reset 作废在途请求。
 */
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { useAsyncTask, type UseAsyncTaskOptions, type UseAsyncTaskResult } from '@/shared/composables/useAsyncTask'

function mountTask<T>(
  runner: () => Promise<T>,
  options: UseAsyncTaskOptions = {},
): { task: UseAsyncTaskResult<T>; unmount: () => void } {
  let task!: UseAsyncTaskResult<T>
  const Comp = defineComponent({
    setup() {
      task = useAsyncTask(runner, options)
      return () => h('div')
    },
  })
  const wrapper = mount(Comp)
  return { task, unmount: () => wrapper.unmount() }
}

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

describe('useAsyncTask', () => {
  it('1. immediate 默认 true：挂载即执行一次，完成后写入 data', async () => {
    const runner = vi.fn().mockResolvedValue({ value: 1 })
    const { task } = mountTask(runner)
    expect(runner).toHaveBeenCalledTimes(1)
    await flushPromises()
    expect(task.loading.value).toBe(false)
    expect(task.error.value).toBeNull()
    expect(task.data.value).toEqual({ value: 1 })
  })

  it('2. immediate:false：挂载不执行，手动 run 才请求', async () => {
    const runner = vi.fn().mockResolvedValue({ value: 2 })
    const { task } = mountTask(runner, { immediate: false })
    expect(runner).toHaveBeenCalledTimes(0)
    await task.run()
    expect(task.data.value).toEqual({ value: 2 })
  })

  it('3. 异常归一为 Error 并写入 error，data 保持 null', async () => {
    const runner = vi.fn().mockRejectedValue(new Error('boom'))
    const { task } = mountTask(runner)
    await flushPromises()
    expect(task.error.value).toBeInstanceOf(Error)
    expect(task.error.value?.message).toBe('boom')
    expect(task.data.value).toBeNull()
    expect(task.loading.value).toBe(false)
  })

  it('4. 过期响应丢弃：先发后至不得覆盖后发结果', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const runner = vi
      .fn<() => Promise<string>>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)

    const { task } = mountTask(runner, { immediate: false })
    const p1 = task.run()
    const p2 = task.run()

    second.resolve('新')
    await p2
    first.resolve('旧')
    await p1

    expect(task.data.value).toBe('新')
    expect(task.loading.value).toBe(false)
  })

  it('5. reset 作废在途请求并清空三态', async () => {
    const pending = deferred<string>()
    const runner = vi.fn<() => Promise<string>>().mockReturnValue(pending.promise)
    const { task } = mountTask(runner, { immediate: false })

    const p = task.run()
    expect(task.loading.value).toBe(true)
    task.reset()
    pending.resolve('迟到结果')
    await p

    expect(task.data.value).toBeNull()
    expect(task.loading.value).toBe(false)
  })
})
