/**
 * LLM 用量模块路由（f3-usage-console 批次③ / tasks 3.1，替换 F0 占位）。
 *
 * SSOT §11.1:717 唯一一条：`/usage` / name=`UsageDashboard` /
 * roles=`['admin','supervisor']` / keepAlive=true（design D2：不新增子路由，
 * 看板与明细同页 Tab）。
 * 视图懒加载（spec R1）；AdminLayout 菜单按 roles 过滤，单路由自动转单子级。
 */
import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '/usage',
    name: 'UsageDashboard',
    component: () => import('./views/UsageDashboard.vue'),
    meta: {
      title: 'LLM 用量',
      icon: 'DataAnalysis',
      roles: ['admin', 'supervisor'],
      keepAlive: true,
    },
  },
]
