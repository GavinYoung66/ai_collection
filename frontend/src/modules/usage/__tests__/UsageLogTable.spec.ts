/**
 * UsageLogTable 用例（f3-usage-console 批次⑥ / tasks 6.1-6.4，对应 tasks 8.3 明细部分）。
 *
 * 覆盖：默认分页参数、status 筛选重置页码、trace_id 精确检索参数、
 * 页内空态（total === 0，含未命中 trace 文案）、非注释 status 原样透出、
 * page_size 变更回第 1 页。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({ listUsageLogs: vi.fn() }))
vi.mock('../api', () => ({ ...apiMocks }))

import UsageLogTable from '../components/UsageLogTable.vue'
import type { UsageLogItem } from '../types'

const RANGE = { start: '2026-10-17T00:00:00.000Z', end: '2026-10-24T00:00:00.000Z' }

function rowItem(over: Partial<UsageLogItem> = {}): UsageLogItem {
  return {
    id: 'l-1',
    trace_id: 'trace-abcdef123456',
    session_id: 's-1',
    provider: 'dashscope',
    model: 'qwen-plus',
    token_usage: { total_tokens: 1234 },
    latency_ms: 812.6,
    confidence: 0.91,
    status: 'ok',
    error_msg: null,
    create_time: '2026-10-23T02:00:00Z',
    ...over,
  }
}

function listResp(items: UsageLogItem[] = [rowItem()]) {
  return { items, total: items.length, page: 1, page_size: 20 }
}

function logStubs() {
  return {
    'el-select': {
      name: 'ElSelect',
      props: ['modelValue', 'size', 'placeholder'],
      emits: ['update:modelValue'],
      template: '<div class="select"><slot /></div>',
    },
    'el-option': {
      name: 'ElOption',
      props: ['label', 'value'],
      template: '<div class="option" />',
    },
    'el-input': {
      name: 'ElInput',
      props: ['modelValue', 'size', 'placeholder', 'type', 'clearable'],
      emits: ['update:modelValue', 'clear', 'keyup'],
      template:
        '<input class="input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'el-button': {
      name: 'ElButton',
      props: ['type', 'size'],
      emits: ['click'],
      template: '<button class="btn" @click="$emit(\'click\')"><slot /></button>',
    },
    'el-alert': {
      props: ['title'],
      template: '<div class="alert">{{ title }}</div>',
    },
    'el-empty': {
      props: ['description'],
      template: '<div class="empty">{{ description }}</div>',
    },
    // 表格只透出 data 与首列插槽内容，且把行数据序列化便于断言原值
    'el-table': {
      name: 'ElTable',
      props: ['data'],
      template:
        '<div class="table"><div v-for="(row, i) in data" :key="i" class="table-row">{{ JSON.stringify(row) }}</div></div>',
    },
    'el-table-column': {
      template: '<div class="column" />',
    },
    'el-pagination': {
      name: 'ElPagination',
      props: ['currentPage', 'pageSize', 'total'],
      emits: ['current-change', 'size-change'],
      template: '<div class="pagination" />',
    },
    'el-tag': {
      props: ['type'],
      template: '<span class="tag"><slot /></span>',
    },
    TraceIdText: {
      props: ['traceId'],
      template: '<code class="trace-id">{{ traceId }}</code>',
    },
  }
}

function mountTable() {
  return mount(UsageLogTable, {
    props: { range: RANGE },
    global: {
      stubs: logStubs(),
      directives: { loading: {} },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.listUsageLogs.mockResolvedValue(listResp())
})

describe('modules/usage/UsageLogTable', () => {
  it('进入明细拉第 1 页 20 条，时间范围与 range 一致、无多余筛选参数', async () => {
    mountTable()
    await flushPromises()

    expect(apiMocks.listUsageLogs).toHaveBeenCalledTimes(1)
    expect(apiMocks.listUsageLogs).toHaveBeenCalledWith({
      start: RANGE.start,
      end: RANGE.end,
      page: 1,
      page_size: 20,
    })
  })

  it('切换 status 筛选 → 重置第 1 页并携带 status', async () => {
    const wrapper = mountTable()
    await flushPromises()

    // 「失败」快捷筛选（spec R5）；值仍为后端原值 failed
    const failedOption = wrapper
      .findAllComponents({ name: 'ElOption' })
      .find((o) => String(o.props('label')).includes('失败'))
    expect(failedOption, '缺少「失败」快捷筛选项').toBeDefined()
    expect(failedOption!.props('value')).toBe('failed')

    wrapper.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'failed')
    await flushPromises()

    const last = apiMocks.listUsageLogs.mock.calls.at(-1)?.[0]
    expect(last).toMatchObject({ page: 1, status: 'failed' })
  })

  it('trace_id 精确检索 → 以 submitted 值下发 trace_id 参数', async () => {
    const wrapper = mountTable()
    await flushPromises()

    wrapper.findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', '  trace-abc  ')
    wrapper.findComponent({ name: 'ElButton' }).trigger('click')
    await flushPromises()

    const last = apiMocks.listUsageLogs.mock.calls.at(-1)?.[0]
    expect(last.trace_id).toBe('trace-abc')
    expect(last.page).toBe(1)
  })

  it('total === 0 → 页内空态；trace 检索未命中时文案区分', async () => {
    apiMocks.listUsageLogs.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
    const wrapper = mountTable()
    await flushPromises()
    expect(wrapper.text()).toContain('所选时间范围内暂无调用明细')
    expect(wrapper.find('.table').exists()).toBe(false)

    wrapper.findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', 'not-exist')
    wrapper.findComponent({ name: 'ElButton' }).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('未找到该 trace_id 的调用记录')
  })

  it('非注释 status（qc_review_exemption / skipped）原样透出，不改写', async () => {
    apiMocks.listUsageLogs.mockResolvedValue(
      listResp([
        rowItem({ id: 'l-2', status: 'qc_review_exemption' }),
        rowItem({ id: 'l-3', status: 'skipped' }),
      ]),
    )
    const wrapper = mountTable()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('qc_review_exemption')
    expect(text).toContain('skipped')
  })

  it('page_size 改为 100 → 回到第 1 页并下发 page_size=100', async () => {
    const wrapper = mountTable()
    await flushPromises()

    wrapper.findComponent({ name: 'ElPagination' }).vm.$emit('size-change', 100)
    await flushPromises()

    const last = apiMocks.listUsageLogs.mock.calls.at(-1)?.[0]
    expect(last).toMatchObject({ page: 1, page_size: 100 })
  })

  it('时间范围变化（父级切换）→ 回到第 1 页重新取数', async () => {
    const wrapper = mountTable()
    await flushPromises()

    await wrapper.setProps({ range: { start: '2026-09-24T00:00:00.000Z', end: RANGE.end } })
    await flushPromises()

    expect(apiMocks.listUsageLogs).toHaveBeenCalledTimes(2)
    const last = apiMocks.listUsageLogs.mock.calls.at(-1)?.[0]
    expect(last).toMatchObject({ page: 1, start: '2026-09-24T00:00:00.000Z' })
  })
})
