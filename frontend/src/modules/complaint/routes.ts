/**
 * complaint 模块路由（嵌套结构：父级"投诉研判" + 三个子路由）。
 *
 * 信息架构（SSOT §11 / 用户反馈 2026-10-23 纠偏）：
 * ├── 一级菜单：投诉研判（el-sub-menu，可折叠）
 * │   ├── 二级：投诉列表      → /complaints          (keepAlive)
 * │   ├── 二级：复核工作台    → /complaints/review    (keepAlive)
 * │   └── （隐藏）投诉详情    → /complaints/:id       (keepAlive: false, 侧边栏不显示)
 *
 * 约束：
 * - 嵌套路由 path 自动拼接：父级 '/complaints' + 子级 '' → '/complaints'；
 *   父级 '/complaints' + 子级 'review' → '/complaints/review'；
 *   父级 '/complaints' + 子级 ':id' → '/complaints/:id'
 *   最终完整路径与原扁平结构**完全一致**，现有 router.push 不受影响
 * - 静态段 'review' 仍在参数段 ':id' 之前（路由匹配优先级）
 * - meta.roles 与 SSOT §3.2 矩阵逐格一致：admin / supervisor / agent
 * - 父级 component 是 PassthroughLayout（仅渲染 <router-view />，不干扰子路由布局）
 * - meta.menu: false 表示该路由不在侧边栏显示（详情页是导航目标，不是菜单入口）
 */
import type { RouteRecordRaw } from 'vue-router'
import { PassthroughLayout } from '@/shared'

export const routes: RouteRecordRaw[] = [
  {
    path: '/complaints',
    name: 'ComplaintGroup',
    component: PassthroughLayout,
    meta: {
      roles: ['admin', 'supervisor', 'agent'],
      title: '投诉研判',
      icon: 'Warning',
    },
    children: [
      {
        path: '',
        name: 'ComplaintList',
        component: () => import('./views/ComplaintList.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'agent'],
          title: '投诉列表',
          keepAlive: true,
          menu: true,
        },
      },
      {
        // 静态段必须写在 :id 参数段之前（路由匹配优先级）
        path: 'review',
        name: 'ComplaintReview',
        component: () => import('./views/ComplaintReview.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'agent'],
          title: '复核工作台',
          keepAlive: true,
          menu: true,
        },
      },
      {
        // 参数段必须声明在静态段之后（路由匹配优先级）
        path: ':id',
        name: 'ComplaintDetail',
        component: () => import('./views/ComplaintDetail.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'agent'],
          title: '投诉详情',
          keepAlive: false,
          menu: false, // 详情页不在侧边栏显示（点击列表行跳转进入）
        },
      },
    ],
  },
]
