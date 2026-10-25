<script setup lang="ts">
/**
 * Agent 日志列表（tasks 3.2）。
 * - props.logs: AgentLogsResponse.logs，按 started_at 升序
 */
import { computed } from 'vue'
import type { AgentLogItem } from '../types'

const props = defineProps<{
  logs: AgentLogItem[]
}>()

const sorted = computed(() =>
  [...props.logs].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime()),
)
</script>

<template>
  <div class="agent-logs">
    <el-empty
      v-if="!logs.length"
      description="暂无 agent 日志"
    />
    <el-table
      v-else
      :data="sorted"
      border
      size="small"
      style="width: 100%"
    >
      <el-table-column
        prop="started_at"
        label="开始时间"
        width="170"
      />
      <el-table-column
        prop="node_name"
        label="节点"
        width="140"
      />
      <el-table-column
        prop="duration_ms"
        label="耗时(ms)"
        width="90"
        align="right"
      />
      <el-table-column
        prop="input_summary"
        label="输入摘要"
        show-overflow-tooltip
      />
      <el-table-column
        prop="output_summary"
        label="输出摘要"
        show-overflow-tooltip
      />
    </el-table>
  </div>
</template>

<style scoped>
.agent-logs { padding: 8px 0; }
</style>
