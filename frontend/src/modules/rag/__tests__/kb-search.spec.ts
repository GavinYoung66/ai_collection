/**
 * KbSearch 检索测试台单测（f2-rag-console 批次⑥ / tasks 8.4）。
 *
 * 照 qc/complaint 先例：vi.mock('../api') + global.stubs 承接 EP 组件
 * （el-form.validate 走可编程 mock；el-input/el-slider/el-select/el-switch
 * 转发 v-model；el-alert 渲染 title；el-card 渲染 slot）。
 *
 * 覆盖点（spec「检索测试台必须固定 answer=false 并展示召回明细」）：
 * - 请求体禁带 answer 键（视图层组装 + api 层双重保证，此处断言视图提交对象）；
 * - 空结果按 results.length===0 判定空态「未找到相关内容」；
 * - degraded=true → 警示徽章展示 message 原文；
 * - top_k 边界 1/20 原样下发；分类「全部」不下发 category，选中后透传；
 * - 校验失败（空 query）行内拦截不发请求。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  searchKb: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

// KbSearch 不调 ElMessage/ElMessageBox，但共享组件 TraceIdText import 了 element-plus，
// 这里提供最小 mock 防模块解析副作用
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

/** el-form.validate 可编程 mock */
const formStubs = vi.hoisted(() => ({ validate: vi.fn() }))

import KbSearch from '../views/KbSearch.vue'

function resultItem(over: Record<string, unknown> = {}) {
  return {
    doc_id: 'd-1',
    title: '催收合规红线手册.md',
    chunk_index: 2,
    content: '禁止向无关第三方泄露债务人隐私信息……',
    score: 0.83,
    rerank_score: 0.91,
    version: 2,
    source: 'v2_ingest',
    summary: null,
    assets: [],
    ...over,
  }
}

function searchResp(over: Record<string, unknown> = {}) {
  return {
    results: [resultItem()],
    degraded: false,
    query_original: '催收流程',
    message: null,
    trace_id: 'tr_0123456789abcdef',
    answer: null,
    citations: [],
    ...over,
  }
}

function searchStubs() {
  return {
    'el-form': {
      template: '<form class="ep-form"><slot /></form>',
      methods: { validate: (...args: unknown[]) => formStubs.validate(...args) },
    },
    'el-form-item': {
      props: ['prop', 'error', 'label'],
      template: '<div class="ep-form-item"><slot /></div>',
    },
    'el-input': {
      props: ['modelValue', 'type', 'rows', 'placeholder'],
      emits: ['update:modelValue', 'input'],
      template:
        '<textarea class="ep-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'el-slider': {
      props: ['modelValue', 'min', 'max', 'step'],
      emits: ['update:modelValue'],
      template:
        '<input class="ep-slider" type="number" :value="modelValue" @input="$emit(\'update:modelValue\', Number($event.target.value))" />',
    },
    'el-select': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template:
        '<select class="ep-select" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
    },
    'el-option': {
      props: ['label', 'value'],
      template: '<option :value="value">{{ label }}</option>',
    },
    'el-switch': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template:
        '<input class="ep-switch" type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    },
    'el-button': {
      props: ['type', 'loading', 'disabled'],
      template:
        '<button class="ep-button" type="button" :disabled="disabled || loading"><slot /></button>',
    },
    'el-alert': {
      props: ['type', 'title', 'closable', 'showIcon'],
      template: '<div class="ep-alert" role="alert">{{ title }}</div>',
    },
    'el-card': { template: '<div class="ep-card"><slot /></div>' },
    TraceIdText: {
      props: ['traceId'],
      template: '<code class="trace-id-stub">{{ traceId }}</code>',
    },
  }
}

function mountSearch() {
  return mount(KbSearch, { global: { stubs: searchStubs() } })
}

/** 填写 query 并点击「发起检索」 */
async function doSearch(wrapper: ReturnType<typeof mountSearch>, query = '催收流程') {
  await wrapper.find('textarea.ep-input').setValue(query)
  await wrapper.find('button.ep-button').trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  formStubs.validate.mockReset().mockResolvedValue(true)
  apiMocks.searchKb.mockResolvedValue(searchResp())
})

describe('modules/rag/KbSearch', () => {
  it('校验失败（空 query）→ 行内拦截，请求不发出', async () => {
    formStubs.validate.mockRejectedValueOnce(new Error('invalid'))
    const wrapper = mountSearch()
    await wrapper.find('button.ep-button').trigger('click')
    await flushPromises()
    expect(apiMocks.searchKb).not.toHaveBeenCalled()
  })

  it('发起检索：请求体仅含 query/top_k/rewrite，禁带 answer 键，分类缺省不下发', async () => {
    const wrapper = mountSearch()
    await doSearch(wrapper)

    expect(apiMocks.searchKb).toHaveBeenCalledTimes(1)
    const body = apiMocks.searchKb.mock.calls[0][0] as Record<string, unknown>
    expect(body).toEqual({ query: '催收流程', top_k: 5, rewrite: false })
    expect('answer' in body).toBe(false)
    expect('category' in body).toBe(false)
  })

  it('结果卡片渲染 content/title/chunk_index/score/rerank_score（score 原样展示）', async () => {
    const wrapper = mountSearch()
    await doSearch(wrapper)
    const text = wrapper.text()
    expect(text).toContain('催收合规红线手册.md')
    expect(text).toContain('chunk #2')
    expect(text).toContain('0.83') // score 原始值
    expect(text).toContain('0.91') // rerank_score 原始值
    expect(text).toContain('禁止向无关第三方泄露债务人隐私信息')
    // 响应 trace_id 展示（后 8 位由 TraceIdText 负责，此处 stub 透传全值）
    expect(wrapper.find('.trace-id-stub').text()).toBe('tr_0123456789abcdef')
  })

  it('空结果（results.length===0）→ 空态「未找到相关内容」', async () => {
    apiMocks.searchKb.mockResolvedValue(searchResp({ results: [] }))
    const wrapper = mountSearch()
    await doSearch(wrapper, '不存在的词')
    expect(wrapper.text()).toContain('未找到相关内容')
    expect(wrapper.find('.ep-card').exists()).toBe(false)
  })

  it('degraded=true → 结果区顶部警示徽章展示 message 原文', async () => {
    apiMocks.searchKb.mockResolvedValue(
      searchResp({ degraded: true, message: 'Embedding 服务不可用，已切换 BM25 兜底召回' }),
    )
    const wrapper = mountSearch()
    await doSearch(wrapper)
    const alert = wrapper.find('.ep-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('Embedding 服务不可用，已切换 BM25 兜底召回')
  })

  it.each([1, 20])('top_k 边界 %d 原样下发', async (topK) => {
    const wrapper = mountSearch()
    await wrapper.find('input.ep-slider').setValue(topK)
    await doSearch(wrapper)
    expect(apiMocks.searchKb.mock.calls[0][0]).toEqual(
      expect.objectContaining({ top_k: topK }),
    )
  })

  it('分类选中后透传 category；rewrite 开关原样下发', async () => {
    const wrapper = mountSearch()
    await wrapper.find('select.ep-select').setValue('case')
    await wrapper.find('input.ep-switch').setValue(true)
    await doSearch(wrapper)
    expect(apiMocks.searchKb.mock.calls[0][0]).toEqual({
      query: '催收流程',
      top_k: 5,
      rewrite: true,
      category: 'case',
    })
  })

  it('检索失败 → message 直出 + 手动重试', async () => {
    apiMocks.searchKb.mockRejectedValueOnce(new Error('系统繁忙，稍后重试（trace: 89abcdef）'))
    const wrapper = mountSearch()
    await doSearch(wrapper)
    expect(wrapper.text()).toContain('检索失败：系统繁忙，稍后重试')

    apiMocks.searchKb.mockResolvedValueOnce(searchResp())
    await wrapper.find('.kbs-btn').trigger('click')
    await flushPromises()
    expect(apiMocks.searchKb).toHaveBeenCalledTimes(2)
  })
})
