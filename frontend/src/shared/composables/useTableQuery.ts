/**
 * 通用分页表格查询（SSOT §19.4）。
 *
 * 零业务语义：只负责 rows / total / page / pageSize / query 与加载三态。
 * - fetcher 由模块注入，负责把「查询条件 + 分页」翻译成具体接口调用；
 * - query 深度变化时自动回到第 1 页并重查（resetPageOnQueryChange，默认 true）；
 * - 竞态保护：序号不匹配的过期响应直接丢弃；
 * - refetchOnActivated：keepAlive 再次激活刷新，首次 activated 跳过（onMounted 已负责）。
 */
import { onActivated, onMounted, ref, watch, type Ref } from 'vue'
import type { PageResult } from '@/shared/types/envelope'

export interface UseTableQueryOptions<T, Q extends object> {
  /** 查询翻译器：把 query + 分页翻译为一次请求 */
  fetcher: (query: Q, page: number, pageSize: number) => Promise<PageResult<T>>
  /** 初始查询条件（必须给默认值，禁止 undefined） */
  initialQuery: Q
  /** 每页条数，默认 20 */
  pageSize?: number
  /** 挂载即查询，默认 true */
  immediate?: boolean
  /** keepAlive 再次激活时刷新，默认 false */
  refetchOnActivated?: boolean
  /** query 变化时回到第 1 页，默认 true */
  resetPageOnQueryChange?: boolean
}

export interface UseTableQueryResult<T, Q extends object> {
  rows: Ref<T[]>
  total: Ref<number>
  page: Ref<number>
  pageSize: Ref<number>
  query: Ref<Q>
  loading: Ref<boolean>
  error: Ref<Error | null>
  /** 按当前条件重查（不改变页码） */
  reload: () => Promise<void>
  /** 跳页并重查 */
  setPage: (page: number) => Promise<void>
  /** 改每页条数（自动回到第 1 页）并重查 */
  setPageSize: (size: number) => Promise<void>
}

export function useTableQuery<T, Q extends object>(
  options: UseTableQueryOptions<T, Q>,
): UseTableQueryResult<T, Q> {
  const rows = ref([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(options.pageSize ?? 20)
  const query = ref(options.initialQuery) as Ref<Q>
  const loading = ref(false)
  const error = ref(null) as Ref<Error | null>

  let seq = 0

  async function reload(): Promise<void> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const result = await options.fetcher(query.value, page.value, pageSize.value)
      if (current !== seq) return
      rows.value = result.items
      total.value = result.total
    } catch (e) {
      if (current !== seq) return
      rows.value = []
      total.value = 0
      error.value = e instanceof Error ? e : new Error(String(e))
    } finally {
      if (current === seq) loading.value = false
    }
  }

  async function setPage(next: number): Promise<void> {
    page.value = next
    await reload()
  }

  async function setPageSize(next: number): Promise<void> {
    pageSize.value = next
    page.value = 1
    await reload()
  }

  // query 变化：默认回到第 1 页再查（筛选条件变更语义）
  watch(
    query,
    () => {
      if (options.resetPageOnQueryChange !== false) page.value = 1
      void reload()
    },
    { deep: true },
  )

  if (options.immediate !== false) {
    onMounted(() => {
      void reload()
    })
  }

  if (options.refetchOnActivated) {
    let firstActivated = true
    onActivated(() => {
      if (firstActivated) {
        firstActivated = false
        return
      }
      void reload()
    })
  }

  return { rows, total, page, pageSize, query, loading, error, reload, setPage, setPageSize }
}
