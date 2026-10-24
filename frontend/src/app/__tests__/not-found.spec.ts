/**
 * NotFound 页面单测（SSOT §19.3）。
 * route-matrix 只验证了「路由存在且不进 AdminLayout」，本文件补上组件层：
 * 文案渲染、返回首页按角色落点、返回上一页。
 */
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import NotFoundPage from '@/app/pages/NotFound.vue'
import { firstAccessibleRoute } from '@/app/router/guards'

// 隔离 store：只关心 role → 落点，不引 auth 的整条加载链路
let currentRole: string | null = 'admin'
vi.mock('@/app/stores/auth', () => ({
  useAuthStore: () => ({ get role() { return currentRole } }),
}))

const stubs = {
  // el-result 透传 title/subTitle 与 extra 插槽，避免整包引入 element-plus
  'el-result': {
    props: ['title', 'subTitle'],
    template:
      '<div class="af-result"><h1>{{ title }}</h1><p>{{ subTitle }}</p><slot name="extra" /></div>',
  },
  'el-button': { template: '<button><slot /></button>' },
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/complaints', component: { template: '<div />' } },
      { path: '/qc/tasks', component: { template: '<div />' } },
      { path: '/kb/documents', component: { template: '<div />' } },
      { path: '/403', component: { template: '<div />' } },
    ],
  })
}

async function mountPage(role: string | null) {
  currentRole = role
  const router = makeRouter()
  await router.push('/does-not-exist')
  await router.isReady()
  const wrapper = mount(NotFoundPage, { global: { plugins: [router], stubs } })
  return { wrapper, router }
}

describe('app/NotFound', () => {
  it('1. 渲染 404 标题与说明，并提供两个出口', async () => {
    const { wrapper } = await mountPage('admin')
    expect(wrapper.find('h1').text()).toBe('404')
    expect(wrapper.text()).toContain('抱歉，您访问的页面不存在或已被移除')
    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).toEqual(['返回首页', '返回上一页'])
  })

  it('2. 「返回首页」按当前角色落到首个可访问路由', async () => {
    const { wrapper, router } = await mountPage('qc_reviewer')
    const home = wrapper.findAll('button').find((b) => b.text() === '返回首页')
    await home!.trigger('click')
    await flushPromises()

    expect(firstAccessibleRoute('qc_reviewer' as never)).toBe('/qc/tasks')
    expect(router.currentRoute.value.path).toBe('/qc/tasks')
  })

  it('3. 角色为空时落到 /403（fail-closed，不猜默认页）', async () => {
    const { wrapper, router } = await mountPage(null)
    const home = wrapper.findAll('button').find((b) => b.text() === '返回首页')
    await home!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/403')
  })
})
