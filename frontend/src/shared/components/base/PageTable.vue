/**
 * PageTable：跨五模块复用的通用分页表格组件。
 *
 * 定位：shared/components/base 层，**禁止 import modules/ 或 app/**（SSOT §7 硬约束，
 * depcruise 会拦）。调用方传入 columns/rows/total，PageTable 只负责渲染与分页事件 emit。
 *
 * 三态互斥：loading（骨架）> error（重试）> empty（空态文字）。
 * 本组件刻意不做：请求发起、状态轮询、业务筛选、业务状态管理——全部由调用方负责。
 */
<script setup lang="ts">
import { computed } from 'vue'

/** 通用列定义——泛型 T 让调用方传入业务类型后 key/prop 有类型提示 */
export interface Column<T> {
  /** 数据字段名（T 上的 key 或任意字符串用于 prop alias 场景） */
  key: keyof T | string
  /** 表头显示文字 */
  label: string
  /** 数据访问别名——prop 有值时优先用 prop.key 取数，否则用 key */
  prop?: keyof T
  width?: string | number
  align?: 'left' | 'center' | 'right'
  /** 可选自定义行渲染——简单表达式级，不引业务组件（SSOT §7） */
  render?: (row: T) => unknown
}

interface Props<T> {
  columns: Column<T>[]
  /** 当前页数据——PageResult.items（SSOT §5.0.1 响应结构） */
  rows: T[]
  /** 全量总数——PageResult.total（分页前置计数后，由后端返回） */
  total: number
  /** 加载中状态（骨架屏）；默认 false */
  loading?: boolean
  /** 空态提示文字；默认 '暂无数据' */
  emptyText?: string
  /** 错误对象——有值时显示 error.message + 重试按钮；默认 null */
  error?: Error | null
  /** 当前页（从 1 起，SSOT §5.0.1 禁止传 page=0）；默认 1 */
  page?: number
  /** 每页条数；默认 20（与后端默认对齐 §5.0.1） */
  pageSize?: number
  /** pageSize 上限；默认 100（与后端上限对齐 §5.0.1） */
  pageSizeMax?: number
  /** 唯一行标识字段名——调用方模块传业务主键字段名；默认 'id' */
  rowKey?: string
}

const props = withDefaults(defineProps<Props<unknown>>(), {
  loading: false,
  emptyText: '暂无数据',
  error: null,
  page: 1,
  pageSize: 20,
  pageSizeMax: 100,
  rowKey: 'id',
})

const emit = defineEmits<{
  (e: 'update:page', v: number): void
  (e: 'update:pageSize', v: number): void
  /** error 三态下的重试按钮点击——调用方决定是否重新发请求 */
  (e: 'retry'): void
}>()

/** 总页数——向上取整；total 为 0 时 pageCount=0，分页控件禁用 */
const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))

/** 三态优先级：loading > error > normal */
const isErrorState = computed(() => !props.loading && !!props.error)
const isEmptyState = computed(() => !props.loading && !props.error && props.rows.length === 0)
const isNormalState = computed(() => !props.loading && !props.error && props.rows.length > 0)

/** 列数据取值——prop 有值优先 */
function cellValue<T>(row: T, col: Column<T>): unknown {
  const k = (col.prop as string) || (col.key as string)
  return row?.[k as keyof T]
}

function onPageChange(p: number) {
  const clamped = Math.max(1, Math.min(p, pageCount.value))
  emit('update:page', clamped)
}

function onPageSizeChange(s: number) {
  const clamped = Math.min(s, props.pageSizeMax)
  // 换 pageSize 时回到第一页（用户预期：每页条数变多/变少后从开头看）
  emit('update:page', 1)
  emit('update:pageSize', clamped)
}

function onRetry() {
  emit('retry')
}

// 类型声明辅助——让模板能识别 Column<unknown>
type AnyColumn = Column<unknown>
</script>

<template>
  <div
    class="page-table"
    role="region"
    aria-live="polite"
  >
    <!-- ① loading 骨架：占位表格 + 分页条 -->
    <template v-if="loading">
      <div class="pt-skeleton">
        <div
          v-for="i in 5"
          :key="i"
          class="pt-skeleton-row"
        >
          <div
            v-for="(_, j) in columns.length"
            :key="j"
            class="pt-skeleton-cell"
          />
        </div>
      </div>
      <div class="pt-pagination pt-pagination--skeleton">
        <div class="pt-skeleton-bar" />
      </div>
    </template>

    <!-- ② error：错误提示 + 重试按钮 -->
    <template v-else-if="isErrorState">
      <div
        class="pt-error"
        role="alert"
      >
        <div
          class="pt-error-icon"
          aria-hidden="true"
        >
          !
        </div>
        <div class="pt-error-msg">
          {{ error?.message || '请求失败' }}
        </div>
        <button
          class="pt-error-retry"
          type="button"
          @click="onRetry"
        >
          重试
        </button>
      </div>
    </template>

    <!-- ③ normal 或 empty：表格体 + 分页 -->
    <template v-else>
      <table
        v-if="isNormalState"
        class="pt-table"
        :aria-rowcount="rows.length"
        :aria-colcount="columns.length"
      >
        <thead>
          <tr>
            <th
              v-for="col in columns as AnyColumn[]"
              :key="String(col.key)"
              class="pt-th"
              :style="{ textAlign: col.align ?? 'left', width: col.width ? `${col.width}px` : undefined }"
            >
              {{ col.label }}
            </th>
            <!-- 调用方可追加表头列（如"操作"） -->
            <slot :name="'table-head-extra'" />
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, idx) in rows"
            :key="String((row as Record<string, unknown>)?.[rowKey] ?? idx)"
            class="pt-tr"
          >
            <td
              v-for="col in columns as AnyColumn[]"
              :key="String(col.key)"
              class="pt-td"
              :style="{ textAlign: col.align ?? 'left' }"
            >
              <!-- 单元格插槽：按列 key 命名（cell-<key>），调用方可用业务组件渲染单元格，
                   未覆盖的列落到默认分支；render 函数输出仍用 String() 强制安全化，防止
                   调用方返回非原始值（如 VNode）触发 toDisplayString → JSON.stringify
                   循环引用中断 Vue patch（2026-10-23 f1-complaint-console 修复） -->
              <slot
                :name="`cell-${String(col.key)}`"
                :row="row"
                :col="col"
                :value="col.render ? (col.render as (r: unknown) => unknown)(row) : cellValue(row, col)"
              >
                <template v-if="col.render">
                  {{ String((col.render as (r: unknown) => unknown)(row) ?? '') }}
                </template>
                <template v-else>
                  {{ String(cellValue(row, col) ?? '-') }}
                </template>
              </slot>
            </td>
            <!-- 调用方可在每行末尾追加操作列（如研判/详情按钮） -->
            <slot
              :name="'table-row-extra'"
              :row="row"
            />
          </tr>
        </tbody>
      </table>

      <!-- empty 空态（rows 为空时渲染空态块 + 分页） -->
      <div
        v-if="isEmptyState"
        class="pt-empty"
      >
        <span class="pt-empty-text">{{ emptyText }}</span>
      </div>

      <!-- 分页控件——仅 total 足够大时才显示；pageSizeMax 与后端对齐 -->
      <div
        v-if="total > 0"
        class="pt-pagination"
        role="navigation"
        aria-label="分页导航"
      >
        <button
          class="pt-page-btn"
          type="button"
          :disabled="page <= 1"
          @click="onPageChange(page - 1)"
        >
          上一页
        </button>
        <span class="pt-page-info">
          第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }} 条
        </span>
        <button
          class="pt-page-btn"
          type="button"
          :disabled="page >= pageCount"
          @click="onPageChange(page + 1)"
        >
          下一页
        </button>
        <select
          class="pt-page-size"
          :value="pageSize"
          aria-label="每页条数"
          @change="onPageSizeChange(Number(($event.target as HTMLSelectElement).value))"
        >
          <option
            v-for="s in [10, 20, 50, pageSizeMax]"
            :key="s"
            :value="s"
          >
            {{ s }} 条/页
          </option>
        </select>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page-table {
  width: 100%;
  font-size: 14px;
  color: #303133;
}

/* —— loading 骨架 —— */
.pt-skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pt-skeleton-row {
  display: flex;
  gap: 8px;
}
.pt-skeleton-cell {
  height: 32px;
  flex: 1;
  background: linear-gradient(90deg, #f2f3f5 25%, #e8eaed 50%, #f2f3f5 75%);
  background-size: 200% 100%;
  animation: pt-shimmer 1.4s infinite;
  border-radius: 4px;
}
@keyframes pt-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* —— error 三态 —— */
.pt-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px 0;
  color: #f56c6c;
}
.pt-error-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #fef0f0;
  color: #f56c6c;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 18px;
}
.pt-error-msg {
  font-size: 14px;
  color: #909399;
}
.pt-error-retry {
  padding: 6px 16px;
  border: 1px solid #f56c6c;
  background: white;
  color: #f56c6c;
  border-radius: 4px;
  cursor: pointer;
}
.pt-error-retry:hover {
  background: #fef0f0;
}

/* —— empty 空态 —— */
.pt-empty {
  padding: 48px 0;
  text-align: center;
}
.pt-empty-text {
  color: #909399;
  font-size: 14px;
}

/* —— 正常表格 —— */
.pt-table {
  width: 100%;
  border-collapse: collapse;
  border: 1px solid #ebeef5;
  background: white;
}
.pt-th,
.pt-td {
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
}
.pt-th {
  background: #fafafa;
  font-weight: 600;
  color: #606266;
  font-size: 13px;
}
.pt-tr:hover {
  background: #f5f7fa;
}

/* —— 分页 —— */
.pt-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
.pt-pagination--skeleton {
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
.pt-skeleton-bar {
  height: 32px;
  width: 260px;
  background: #f2f3f5;
  border-radius: 4px;
}
.pt-page-btn {
  padding: 4px 12px;
  border: 1px solid #dcdfe6;
  background: white;
  color: #606266;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}
.pt-page-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pt-page-info {
  color: #606266;
  font-size: 13px;
}
.pt-page-size {
  padding: 4px 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  font-size: 13px;
  background: white;
}
</style>
