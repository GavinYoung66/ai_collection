/**
 * AsyncState 单测（SSOT §19.7）。
 * 覆盖：三态互斥优先级、错误文案归一（Error / string）、自定义 #error #empty 插槽、
 * retry 事件、默认插槽仅在正常态渲染。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AsyncState from '@/shared/components/base/AsyncState.vue'

const content = { template: '<div class="slot-content">真实内容</div>' }

describe('shared/AsyncState', () => {
  it('1. loading 优先：即使同时传 error 也只渲染加载态', () => {
    const w = mount(AsyncState, {
      props: { loading: true, error: new Error('不该出现'), loadingText: '检索中…' },
      slots: { default: content },
    })
    expect(w.text()).toContain('检索中…')
    expect(w.text()).not.toContain('不该出现')
    expect(w.find('.slot-content').exists()).toBe(false)
  })

  it('2. error 态：Error 取 message 并带 errorPrefix；不渲染默认插槽', () => {
    const w = mount(AsyncState, {
      props: { error: new Error('网关超时'), errorPrefix: '检索失败：' },
      slots: { default: content },
    })
    expect(w.find('[role="alert"]').text()).toContain('检索失败：网关超时')
    expect(w.find('.slot-content').exists()).toBe(false)
  })

  it('3. error 态：字符串错误直出（拦截器已加工文案场景）', () => {
    const w = mount(AsyncState, { props: { error: 'B0006 知识库不可用 (trace ab12cd34)' } })
    expect(w.find('[role="alert"]').text()).toContain('B0006 知识库不可用 (trace ab12cd34)')
  })

  it('4. 自定义 #error 插槽可拿到 message 并覆盖默认文案', () => {
    const w = mount(AsyncState, {
      props: { error: new Error('原始错误') },
      slots: {
        error: `<template #error="{ message }"><div class="custom-err">已加工：{{ message }}</div></template>`,
      },
    })
    expect(w.find('.custom-err').text()).toBe('已加工：原始错误')
  })

  it('5. empty 态：默认文案与自定义 #empty 插槽', () => {
    const w1 = mount(AsyncState, { props: { empty: true, emptyText: '暂无数据' } })
    expect(w1.text()).toContain('暂无数据')

    const w2 = mount(AsyncState, {
      props: { empty: true },
      slots: { empty: '<div class="custom-empty">词典暂不可用</div>' },
    })
    expect(w2.find('.custom-empty').text()).toBe('词典暂不可用')
  })

  it('6. 正常态：渲染默认插槽；点重试 emit retry', async () => {
    const normal = mount(AsyncState, { slots: { default: content } })
    expect(normal.find('.slot-content').text()).toBe('真实内容')

    const errored = mount(AsyncState, { props: { error: 'x' } })
    await errored.find('.as-retry').trigger('click')
    expect(errored.emitted('retry')).toHaveLength(1)
  })
})
