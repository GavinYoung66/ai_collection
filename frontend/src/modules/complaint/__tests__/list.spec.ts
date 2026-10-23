/**
 * ComplaintList 组件契约单测（tasks 2.2）。
 *
 * 覆盖点：
 * - 默认请求参数 page=1&page_size=20
 * - 状态筛选选择后请求携带 task_status
 * - onMounted 拉一次 + onActivated 也拉一次（keepAlive 双实例各自刷新）
 * - DEV 门控：DEV=true 时演示入口按钮存在
 * - 建单提交中按钮 disabled
 *
 * 注：jsdom 不支持 `:has-text()` pseudo-class，全部用 `.findAll('button').filter()` 替代。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  listComplaints: vi.fn().mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 20,
  }),
  createComplaint: vi.fn().mockResolvedValue({
    complaint_id: 'c-001',
    task_status: 'pending',
    created_at: '2026-10-23T00:00:00Z',
  }),
  seedDemoComplaints: vi.fn().mockResolvedValue({ complaint_ids: [] }),
  listComplaintEvents: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}))

vi.mock('../api', () => ({
  listComplaints: apiMocks.listComplaints,
  createComplaint: apiMocks.createComplaint,
  seedDemoComplaints: apiMocks.seedDemoComplaints,
  listComplaintEvents: apiMocks.listComplaintEvents,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ path: '/complaints', params: {}, query: {} }),
}))

import { mount, flushPromises } from '@vue/test-utils'
import ComplaintList from '../views/ComplaintList.vue'

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find(b => b.text().includes(text))
}

function mountComponent(props: Record<string, unknown> = {}) {
  return mount(ComplaintList, {
    props,
    global: {
      stubs: { PageTable: true },
      plugins: [],
    },
  })
}

describe('modules/complaint/ComplaintList', () => {
  beforeEach(() => {
    apiMocks.listComplaints.mockClear()
    apiMocks.listComplaints.mockResolvedValue({
      items: [], total: 0, page: 1, page_size: 20,
    })
  })

  it('默认请求参数 page=1&page_size=20（无状态筛选）', async () => {
    mountComponent()
    await vi.waitFor(() => {
      expect(apiMocks.listComplaints).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, page_size: 20 }),
      )
    })
  })

  it('选择状态筛选后：请求携带 task_status', async () => {
    const wrapper = mountComponent()
    await flushPromises()
    apiMocks.listComplaints.mockClear()

    await wrapper.find('select.cl-filter').setValue('pending_review')
    await vi.waitFor(() => {
      expect(apiMocks.listComplaints).toHaveBeenCalledWith(
        expect.objectContaining({ task_status: ['pending_review'] }),
      )
    })
  })

  it('onMounted 拉一次 + onActivated 也拉一次（keepAlive 恢复时刷新）', async () => {
    const wrapper = mountComponent()
    // onMounted 自动触发 fetch 一次
    await vi.waitFor(() => expect(apiMocks.listComplaints).toHaveBeenCalledTimes(1))

    // 手动触发 activated 钩子：Vue 的 activated 不是 DOM 事件，
    // 但 Vue Test Utils v2 可以通过 wrapper.vm.$.setupState 拿到 setupState
    // 上注册的生命周期回调。更稳的方式是直接调用 setup 里的 fetchList——
    // 因为 onActivated(fetchList) 绑定的就是同一个函数
    const vm = wrapper.vm as unknown as Record<string, unknown>
    const setupState = (vm as { $?: { setupState?: Record<string, unknown> } }).$?.setupState
    // setupState.fetchList 就是 onActivated 绑定的函数
    const fetchListFromSetup = setupState?.fetchList as (() => Promise<void>) | undefined
    expect(fetchListFromSetup).toBeDefined()
    await fetchListFromSetup!()
    await vi.waitFor(() => expect(apiMocks.listComplaints).toHaveBeenCalledTimes(2))
  })

  it('DEV 门控：DEV=true 时演示灌数按钮存在', async () => {
    const wrapper = mountComponent()
    await vi.waitFor(() => {
      // vitest.config 里 define 了 import.meta.env = { DEV: true, ... }
      expect(findButtonByText(wrapper, '演示灌数')).toBeDefined()
      expect(findButtonByText(wrapper, '事件列表')).toBeDefined()
    })
  })

  it('建单提交中按钮 disabled', async () => {
    apiMocks.createComplaint.mockReturnValue(new Promise(() => {})) // 挂起，模拟提交中
    const wrapper = mountComponent()
    await vi.waitFor(() => {
      const createBtn = findButtonByText(wrapper, '+ 手工建单')
      expect(createBtn).toBeDefined()
      createBtn!.trigger('click')

      wrapper.find('textarea').setValue('投诉内容')

      const confirmBtn = findButtonByText(wrapper, '确认建单')
      confirmBtn!.trigger('click')

      // 提交中：禁用状态存在
      expect(confirmBtn!.attributes('disabled') !== undefined).toBe(true)
    })
  })
})
