<script setup lang="ts">
/**
 * AsyncState：非表格场景的通用三态容器（SSOT §19.7）。
 *
 * 定位：shared/components/base 层，**禁止 import modules/ 或 app/**（SSOT §7）。
 * 三态互斥：loading > error > empty > default。
 * 零业务语义：不发起请求、不决定文案之外的任何行为；错误文案由调用方（或拦截器）加工后传入。
 *
 * 默认插槽仅在「非加载、无错误、非空」时渲染。
 */
export interface AsyncStateProps {
  /** 加载中 */
  loading?: boolean
  /** 错误：Error 或已加工文案；有值即进入错误态 */
  error?: Error | string | null
  /** 空态：显式置 true 才渲染（默认 false，避免误伤「无内容即无区块」的页面） */
  empty?: boolean
  loadingText?: string
  emptyText?: string
  /** 错误文案前缀，如「检索失败：」 */
  errorPrefix?: string
}

const props = withDefaults(defineProps<AsyncStateProps>(), {
  loading: false,
  error: null,
  empty: false,
  loadingText: '加载中…',
  emptyText: '暂无数据',
  errorPrefix: '',
})

defineEmits<{ (e: 'retry'): void }>()

/** 错误文案归一：Error 取 message，字符串直出 */
function errorText(): string {
  if (!props.error) return ''
  return typeof props.error === 'string' ? props.error : props.error.message
}
</script>

<template>
  <div
    class="async-state"
    aria-live="polite"
  >
    <div
      v-if="loading"
      class="as-block as-loading"
    >
      <span class="as-spinner" />
      <span>{{ loadingText }}</span>
    </div>

    <div
      v-else-if="error"
      class="as-block as-error"
      role="alert"
    >
      <div
        class="as-error-icon"
        aria-hidden="true"
      >
        !
      </div>
      <div class="as-error-msg">
        <slot
          name="error"
          :message="errorText()"
        >
          {{ errorPrefix }}{{ errorText() }}
        </slot>
      </div>
      <button
        class="as-retry"
        type="button"
        @click="$emit('retry')"
      >
        重试
      </button>
    </div>

    <div
      v-else-if="empty"
      class="as-block as-empty"
    >
      <slot name="empty">
        {{ emptyText }}
      </slot>
    </div>

    <slot
      v-else
      name="default"
    />
  </div>
</template>

<style scoped>
.async-state {
  width: 100%;
}
.as-block {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  font-size: 14px;
  color: #909399;
}
.as-loading {
  flex-direction: row;
}
.as-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #dcdfe6;
  border-top-color: #909399;
  border-radius: 50%;
  animation: as-spin 0.8s linear infinite;
}
@keyframes as-spin {
  to {
    transform: rotate(360deg);
  }
}
.as-error {
  flex-direction: column;
  color: #f56c6c;
}
.as-error-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #fef0f0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 18px;
}
.as-error-msg {
  font-size: 14px;
  color: #909399;
  text-align: center;
  word-break: break-all;
}
.as-retry {
  padding: 6px 16px;
  border: 1px solid #f56c6c;
  background: white;
  color: #f56c6c;
  border-radius: 4px;
  cursor: pointer;
}
.as-retry:hover {
  background: #fef0f0;
}
.as-empty {
  padding: 48px 0;
}
</style>
