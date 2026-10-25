/**
 * shared 层唯一公共出口（SSOT §19.7）。
 *
 * 约束：modules/ 只能从 `@/shared` 导入（depcruise `no-modules-deep-import-shared`
 * 强制），不得深链到具体文件。**唯一例外：`@/shared/request`**——api 层单测以
 * `vi.mock('@/shared/request')` 按路径拦截，走 barrel 会让 mock 失效而出现整批假绿；
 * 该例外在 depcruise 规则的 pathNot 中显式登记，不是遗漏。
 *
 * shared 自身不 import modules/ 与 app/（SSOT §7）。
 */
export * from './types/common'
export * from './types/envelope'

export * from './utils/constants'
export * from './utils/error-map'
export * from './utils/path'
export * from './utils/uuid'

export * from './permissions'

export * from './composables/useAsyncTask'
export * from './composables/usePoller'
export * from './composables/useTableQuery'

export * from './components/base'
