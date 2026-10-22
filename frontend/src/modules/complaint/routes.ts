import type { RouteRecordRaw } from 'vue-router'
import ModulePlaceholder from '@/shared/components/base/ModulePlaceholder.vue'

/**
 * 智能投诉模块路由（F0 仅占位，F1 扩展子路由）。
 * meta.roles 与 SSOT §3.2 矩阵逐格一致：admin / supervisor / agent。
 */
export const routes: RouteRecordRaw[] = [
  {
    path: '/complaints',
    name: 'ComplaintList',
    component: ModulePlaceholder,
    props: { title: '智能投诉' },
    meta: { title: '智能投诉', icon: 'ChatDotRound', roles: ['admin', 'supervisor', 'agent'] },
  },
]
