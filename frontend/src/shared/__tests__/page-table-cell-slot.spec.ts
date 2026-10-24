/**
 * PageTable cell-<key> 插槽单测（SSOT §19.7）。
 * 目的：确保「按列 key 覆盖单元格」真正生效，且未覆盖的列仍走默认分支。
 * 之前 kb-documents.spec 只 stub 了 StatusTag，没有任何断言证明插槽被使用——
 * 插槽名拼错时测试仍全绿，本文件补上这条契约。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PageTable from '@/shared/components/base/PageTable.vue'
import type { Column } from '@/shared/components/base/PageTable.vue'

interface Row {
  id: string
  title: string
  status: string
  plain: string | null
}

// PageTable 的 props 声明为 Props<unknown>（组件内不感知业务类型），
// 因此测试侧列定义也按 Column<unknown> 声明，render 内自行收窄。
const columns: Column<unknown>[] = [
  { key: 'title', label: '标题', render: (r) => (r as Row).title },
  { key: 'status', label: 'status', render: (r) => (r as Row).status },
  { key: 'plain', label: '备注', render: (r) => (r as Row).plain },
  // 无 render 的列：取值走 cellValue，缺失时回落 '-'
  { key: 'absent', label: '缺省' },
]

const rows: Row[] = [{ id: 'd1', title: '话术文档', status: 'processing', plain: null }]

const baseProps = {
  columns,
  rows,
  total: 1,
  page: 1,
  pageSize: 20,
  loading: false,
  error: null,
  rowKey: 'id',
}

describe('shared/PageTable cell-<key> 插槽', () => {
  it('1. 命中列走自定义插槽（可渲染业务组件），不再走 render 的字符串兜底', () => {
    const w = mount(PageTable, {
      props: baseProps,
      slots: {
        'cell-status': `<template #cell-status="{ row }"><span class="tag">TAG:{{ row.status }}</span></template>`,
      },
    })
    const tags = w.findAll('.tag')
    expect(tags, 'cell-status 插槽必须被使用').toHaveLength(1)
    expect(tags[0].text()).toBe('TAG:processing')
  })

  it('2. 未覆盖的列仍走默认分支：render 输出原样，无 render 缺失时回落 "-"', () => {
    const w = mount(PageTable, {
      props: baseProps,
      slots: {
        'cell-status': `<template #cell-status="{ row }"><span class="tag">{{ row.status }}</span></template>`,
      },
    })
    expect(w.find('.tag').exists()).toBe(true)

    const cells = w.findAll('tbody td').map((td) => td.text())
    expect(cells[0], 'title 列走 render').toBe('话术文档')
    // 契约（当前实现，勿在未确认前改）：有 render 且返回 null → 空串；无 render 且取值为空 → '-'
    expect(cells[2], 'render 返回 null 渲染空串').toBe('')
    expect(cells[3], '无 render 的列取值缺失回落 "-"').toBe('-')
  })

  it('3. 插槽可拿到 col 与 value 上下文', () => {
    const w = mount(PageTable, {
      props: baseProps,
      slots: {
        'cell-title': `<template #cell-title="{ col, value }"><i class="ctx">{{ col.label }}|{{ value }}</i></template>`,
      },
    })
    expect(w.find('.ctx').text()).toBe('标题|话术文档')
  })
})
