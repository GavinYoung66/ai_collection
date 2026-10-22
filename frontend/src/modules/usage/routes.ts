import type { RouteRecordRaw } from 'vue-router'
import ModulePlaceholder from '@/shared/components/base/ModulePlaceholder.vue'

/**
 * LLM 用量模块路由（F0 仅占位，F3 实现真实页面）。
 * meta.roles 与 SSOT §3.2 矩阵逐格一致：admin / supervisor。
 */
export const routes: RouteRecordRaw[] = [
  {
    path: '/usage',
    name: 'UsageDashboard',
    component: ModulePlaceholder,
    props: { title: 'LLM 用量' },
    meta: { title: 'LLM 用量', icon: 'DataAnalysis', roles: ['admin', 'supervisor'] },
  },
]
