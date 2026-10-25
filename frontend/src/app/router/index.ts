import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import AdminLayout from '@/app/layouts/AdminLayout.vue'
import Login from '@/app/pages/Login.vue'
import Forbidden from '@/app/pages/Forbidden.vue'
import NotFound from '@/app/pages/NotFound.vue'
import { BizModule as complaintModule } from '@/modules/complaint'
import { BizModule as qcModule } from '@/modules/qc'
import { BizModule as ragModule } from '@/modules/rag'
import { BizModule as usageModule } from '@/modules/usage'
import { BizModule as systemModule } from '@/modules/system'
import { setupGuards } from './guards'

/**
 * 路由表（唯一装配点，SSOT §7.1 / §11 / D3）。
 * 静态 import 汇总各 BizModule.routes，作为 AdminLayout 的 children；
 * 禁止使用 router.addRoute（编译期静态装配）。
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AdminLayout,
    children: [
      ...complaintModule.routes,
      ...qcModule.routes,
      ...ragModule.routes,
      ...usageModule.routes,
      ...systemModule.routes,
    ],
  },
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { title: '登录' },
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: Forbidden,
    meta: { title: '无权限' },
  },
  {
    // 未知路径兜底（SSOT §19.3）：必须置于所有具名路由之后，
    // 否则会抢先匹配 /login、/403 等具体路径。
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: NotFound,
    meta: { title: '页面不存在' },
  },
]

export const router = createRouter({
  // base 三处同源：vite.config.ts base + 此处 import.meta.env.BASE_URL + 部署前缀 /admin/
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

// 注册全局前置守卫（模块内导入即装配）
setupGuards(router)
