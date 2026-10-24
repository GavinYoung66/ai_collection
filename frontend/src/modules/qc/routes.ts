/**
 * 合规质检模块路由（f2-qc-console 批次2 / tasks 3.1，替换 F0 占位）。
 *
 * 信息架构（嵌套结构，照 complaint 先例，SSOT §11.1）：
 * ├── 一级菜单：合规质检（el-sub-menu，可折叠；父级 roles 为六条子路由收窄后的并集，
 * │   仅供侧边栏一级过滤；守卫读 to.meta，子级覆盖父级，收窄在子级生效）
 * │   ├── 二级：质检任务台    → /qc/tasks               (keepAlive)
 * │   ├── 二级：质检报告      → /qc/records             (keepAlive，必须先于 records/:sessionId)
 * │   ├── （隐藏）报告详情    → /qc/records/:sessionId  (keepAlive: false, 侧边栏不显示)
 * │   ├── 二级：人工复核      → /qc/reviews             (keepAlive)
 * │   ├── 二级：规则管理      → /qc/rules               (keepAlive)
 * │   └── 二级：告警流        → /qc/alerts              (keepAlive)
 *
 * meta.roles 按 design D3 收窄表（与后端 require_roles 词表对齐，回填 SSOT §11.1/§3.2 注③）：
 * - rules   = ['admin']                          （rules/active=admin/ai_trainer、apply=admin）
 * - alerts  = ['admin','supervisor']             （alerts=supervisor/admin）
 * - reviews = ['supervisor','qc_reviewer']       （reviews=qc_reviewer/supervisor，词表不含 admin）
 * - 其余三条 = ['admin','supervisor','qc_reviewer']
 * 收窄属纯可见性修正，授权边界仍在后端；详情页人审按钮组按 §5.0 F 按钮级置灰（批次5 落地）。
 *
 * 视图一律 () => import() 懒加载（spec：进入对应路由才加载 chunk）；
 * 当前为批次2 骨架视图（仅 defineOptions name + 占位文案），批次3-5 填充实现。
 */
import type { RouteRecordRaw } from 'vue-router'
import PassthroughLayout from '@/shared/components/base/PassthroughLayout.vue'

export const routes: RouteRecordRaw[] = [
  {
    path: '/qc',
    name: 'QcGroup',
    component: PassthroughLayout,
    // 父级无独立页面：直访 /qc 重定向任务台，避免「有布局、无内容」白页
    redirect: '/qc/tasks',
    meta: {
      roles: ['admin', 'supervisor', 'qc_reviewer'],
      title: '合规质检',
      icon: 'CircleCheck',
    },
    children: [
      {
        path: 'tasks',
        name: 'QcTaskBoard',
        component: () => import('./views/QcTaskBoard.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'qc_reviewer'],
          title: '质检任务台',
          icon: 'CircleCheck',
          keepAlive: true,
          menu: true,
        },
      },
      {
        // 静态段必须写在 :sessionId 参数段之前（路由匹配优先级，SSOT §11.1 排序约束）
        path: 'records',
        name: 'QcRecordList',
        component: () => import('./views/QcRecordList.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'qc_reviewer'],
          title: '质检报告',
          icon: 'Document',
          keepAlive: true,
          menu: true,
        },
      },
      {
        // 参数段声明在静态段之后；详情页不缓存、不进侧边栏（点击列表行跳转进入）
        path: 'records/:sessionId',
        name: 'QcRecordDetail',
        component: () => import('./views/QcRecordDetail.vue'),
        meta: {
          roles: ['admin', 'supervisor', 'qc_reviewer'],
          title: '报告详情',
          icon: 'Document',
          keepAlive: false,
          menu: false,
        },
      },
      {
        path: 'reviews',
        name: 'QcReview',
        component: () => import('./views/QcReview.vue'),
        meta: {
          roles: ['supervisor', 'qc_reviewer'],
          title: '人工复核',
          icon: 'EditPen',
          keepAlive: true,
          menu: true,
        },
      },
      {
        path: 'rules',
        name: 'QcRules',
        component: () => import('./views/QcRules.vue'),
        meta: {
          roles: ['admin'],
          title: '规则管理',
          icon: 'Collection',
          keepAlive: true,
          menu: true,
        },
      },
      {
        path: 'alerts',
        name: 'QcAlerts',
        component: () => import('./views/QcAlerts.vue'),
        meta: {
          roles: ['admin', 'supervisor'],
          title: '告警流',
          icon: 'Bell',
          keepAlive: true,
          menu: true,
        },
      },
    ],
  },
]
