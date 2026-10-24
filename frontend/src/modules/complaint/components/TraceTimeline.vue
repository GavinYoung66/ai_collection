<script setup lang="ts">
/**
 * Trace 事件时间线（tasks 3.2）。
 * - props.events: TraceResponse.events，按 started_at 升序渲染
 */
import { computed } from 'vue'
import type { TraceEventItem } from '../types'

const props = defineProps<{
  events: TraceEventItem[]
}>()

// 按 started_at 升序
const sorted = computed(() =>
  [...props.events].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime()),
)
</script>

<template>
  <div class="trace-timeline">
    <el-empty v-if="!events.length" description="暂无 trace 事件" />
    <el-timeline v-else>
      <el-timeline-item
        v-for="(evt, i) in sorted"
        :key="i"
        :timestamp="evt.started_at"
        placement="top"
      >
        <div class="trace-event">
          <el-tag size="small" type="info">{{ evt.category }}</el-tag>
          <strong class="event-name">{{ evt.name }}</strong>
          <span class="event-duration">{{ evt.duration_ms }}ms</span>
          <el-tag
            :type="evt.status === 'success' ? 'success' : evt.status === 'error' ? 'danger' : 'warning'"
            size="small"
          >
            {{ evt.status }}
          </el-tag>
          <p class="event-summary">{{ evt.summary }}</p>
        </div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<style scoped>
.trace-timeline { padding: 8px 0; }
.trace-event { display: flex; flex-direction: column; gap: 4px; }
.event-name { font-size: 14px; color: #303133; }
.event-duration { color: #909399; font-size: 12px; }
.event-summary { margin: 2px 0 0; color: #606266; font-size: 13px; }
</style>
