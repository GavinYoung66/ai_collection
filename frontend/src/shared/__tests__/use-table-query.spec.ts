/**
 * useTableQuery 单测（SSOT §19.4）。
 * 覆盖：挂载查询、翻页、改页长回到第 1 页、筛选变更重置页码、错误处理、过期响应丢弃。
 */
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { useTableQuery, type UseTableQueryResult } from '@/shared/composables/useTableQuery'
import type { PageResult } from '@/shared/types/envelope'

interface Row {
  id: number
}
interface Query {
  keyword: string
}

const page = (items: Row[], total = items.length): PageResult<Row> => ({
  items,
  total,
  page: 1,
  page_size: 20,
})

function mountQuery(
  fetcher: (query: Query, page: number, pageSize: number) => Promise<PageResult<Row>>,
  options: { pageSize?: number; refetchOnActivated?: boolean } = {},
): { table: UseTableQueryResult<Row, Query> } {
  let table!: UseTableQueryResult<Row, Query>
  const Comp = defineComponent({
    setup() {
      table = useTableQuery<Row, Query>({
        fetcher,
        initialQuery: { keyword: '' },
        pageSize: options.pageSize ?? 20,
        refetchOnActivated: options.refetchOnActivated,
      })
      return () => h('div')
    },
  })
  mount(Comp)
  return { table }
}

describe('useTableQuery', () => {
  it('1. 挂载即按初始条件查询，写入 rows/total', async () => {
    const fetcher = vi.fn().mockResolvedValue(page([{ id: 1 }, { id: 2 }], 2))
    const { table } = mountQuery(fetcher)
    await flushPromises()

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher).toHaveBeenCalledWith({ keyword: '' }, 1, 20)
    expect(table.rows.value).toHaveLength(2)
    expect(table.total.value).toBe(2)
    expect(table.loading.value).toBe(false)
  })

  it('2. setPage 以新页码重查，不动 query', async () => {
    const fetcher = vi.fn().mockResolvedValue(page([{ id: 1 }], 100))
    const { table } = mountQuery(fetcher)
    await flushPromises()

    await table.setPage(3)
    expect(fetcher).toHaveBeenLastCalledWith({ keyword: '' }, 3, 20)
    expect(table.page.value).toBe(3)
  })

  it('3. setPageSize 改页长并回到第 1 页', async () => {
    const fetcher = vi.fn().mockResolvedValue(page([]))
    const { table } = mountQuery(fetcher)
    await flushPromises()

    await table.setPage(4)
    await table.setPageSize(50)
    expect(table.page.value).toBe(1)
    expect(fetcher).toHaveBeenLastCalledWith({ keyword: '' }, 1, 50)
  })

  it('4. query 深度变更：自动回到第 1 页并重查', async () => {
    const fetcher = vi.fn().mockResolvedValue(page([]))
    const { table } = mountQuery(fetcher)
    await flushPromises()

    await table.setPage(2)
    table.query.value.keyword = '逾期'
    await nextTick()
    await flushPromises()

    expect(table.page.value).toBe(1)
    expect(fetcher).toHaveBeenLastCalledWith({ keyword: '逾期' }, 1, 20)
  })

  it('5. 请求失败：error 写入、rows/total 清空、loading 复位', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('列表加载失败'))
    const { table } = mountQuery(fetcher)
    await flushPromises()

    expect(table.error.value?.message).toBe('列表加载失败')
    expect(table.rows.value).toEqual([])
    expect(table.total.value).toBe(0)
    expect(table.loading.value).toBe(false)
  })

  it('6. 过期响应丢弃：快速翻页时旧页数据不得覆盖新页', async () => {
    let resolveFirst!: (v: PageResult<Row>) => void
    const first = new Promise<PageResult<Row>>((r) => {
      resolveFirst = r
    })
    const fetcher = vi
      .fn<(query: Query, page: number, pageSize: number) => Promise<PageResult<Row>>>()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(page([{ id: 99 }], 100))

    const { table } = mountQuery(fetcher)
    // 首次挂载请求仍在途，立即翻页发出第二个请求
    await table.setPage(2)
    resolveFirst(page([{ id: 1 }], 100))
    await flushPromises()

    expect(table.rows.value).toEqual([{ id: 99 }])
  })
})
