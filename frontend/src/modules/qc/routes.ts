import type { RouteRecordRaw } from 'vue-router'
import ModulePlaceholder from '@/shared/components/base/ModulePlaceholder.vue'

/**
 * 合规质检模块路由（F0 仅占位，F2 扩展子路由）。
 * meta.roles 与 SSOT §3.2 矩阵逐格一致：admin / supervisor / qc_reviewer。
 */
export const routes: RouteRecordRaw[] = [
  {
    path: '/qc/tasks',
    name: 'QcTaskBoard',
    component: ModulePlaceholder,
    props: { title: '合规质检' },
    meta: { title: '合规质检', icon: 'CircleCheck', roles: ['admin', 'supervisor', 'qc_reviewer'] },
  },
]
