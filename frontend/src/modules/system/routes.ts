import type { RouteRecordRaw } from 'vue-router'
import ModulePlaceholder from '@/shared/components/base/ModulePlaceholder.vue'

/**
 * 系统管理模块路由（F0 仅占位，P1 实现用户管理）。
 * meta.roles 与 SSOT §3.2 矩阵逐格一致：admin。
 */
export const routes: RouteRecordRaw[] = [
  {
    path: '/system/users',
    name: 'SystemUsers',
    component: ModulePlaceholder,
    props: { title: '系统管理' },
    meta: { title: '系统管理', icon: 'Setting', roles: ['admin'] },
  },
]
