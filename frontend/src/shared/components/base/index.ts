/**
 * base 层统一导出入口——补齐此前 StatusTag/TraceIdText/ModulePlaceholder 无统一 index 的空缺。
 * SSOT §7：shared/ 组件统一通过 base/index.ts 导入，禁止引各组件文件本身。
 */
export { default as StatusTag } from './StatusTag.vue'
export { default as TraceIdText } from './TraceIdText.vue'
export { default as ModulePlaceholder } from './ModulePlaceholder.vue'
export { default as PageTable } from './PageTable.vue'
export { default as PassthroughLayout } from './PassthroughLayout.vue'
export { default as AsyncState } from './AsyncState.vue'
export type { Column } from './PageTable.vue'
export type { AsyncStateProps } from './AsyncState.vue'
