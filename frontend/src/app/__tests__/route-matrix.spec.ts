/**
 * 角色矩阵自动化基线（SSOT §3.2 / tasks 6.4）。
 *
 * 目的：把批次 6 的「25 格人工实测」固化为可回归断言。
 * 两重校验：
 *   ① 五模块 routes.ts 的 meta.roles 与 SSOT §3.2 逐格一致；
 *   ② app/router 实际装配的路由与模块导出一致（防装配漏项/错位）。
 * 二者同源是「菜单可见 = URL 可达」的前提：AdminLayout 菜单由路由表生成，
 * 守卫读同一份 meta.roles，任一侧偏移都会导致「可见但被拒」。
 */
import { describe, it, expect } from 'vitest'
import type { RouteRecordRaw } from 'vue-router'
import type { Role } from '@/shared/types/common'
import { routes as complaintRoutes } from '@/modules/complaint/routes'
import { routes as qcRoutes } from '@/modules/qc/routes'
import { routes as ragRoutes } from '@/modules/rag/routes'
import { routes as usageRoutes } from '@/modules/usage/routes'
import { routes as systemRoutes } from '@/modules/system/routes'
import { router } from '@/app/router/index'

/** SSOT §3.2 角色矩阵（唯一事实源，逐格手抄，改动必须同步此处） */
const MATRIX: Record<string, Role[]> = {
  '/complaints': ['admin', 'supervisor', 'agent'],
  '/qc/tasks': ['admin', 'supervisor', 'qc_reviewer'],
  '/kb/documents': ['admin', 'ai_trainer'],
  '/usage': ['admin', 'supervisor'],
  '/system/users': ['admin'],
}

const ROLES: Role[] = ['admin', 'supervisor', 'agent', 'qc_reviewer', 'ai_trainer']

/** 五模块导出路由汇总，按 path 建索引 */
const MODULE_ROUTES: RouteRecordRaw[] = [
  ...complaintRoutes,
  ...qcRoutes,
  ...ragRoutes,
  ...usageRoutes,
  ...systemRoutes,
]

describe('角色矩阵（5 模块 × 5 角色 = 25 格）', () => {
  it('1. 五模块导出路由数量与 SSOT 一致（F0 各 1 条）', () => {
    expect(complaintRoutes).toHaveLength(1)
    expect(qcRoutes).toHaveLength(1)
    expect(ragRoutes).toHaveLength(1)
    expect(usageRoutes).toHaveLength(1)
    expect(systemRoutes).toHaveLength(1)
    expect(MODULE_ROUTES.map((r) => r.path).sort()).toEqual(Object.keys(MATRIX).sort())
  })

  it('2. 每条路由的 meta.title 非空、meta.roles 与 SSOT §3.2 逐格一致', () => {
    for (const route of MODULE_ROUTES) {
      const expected = MATRIX[route.path]
      expect(expected, `未在 SSOT 矩阵中登记的路径：${route.path}`).toBeDefined()
      expect(route.meta?.title, `${route.path} 缺 meta.title`).toBeTruthy()
      // 集合比较（顺序不敏感），避免因书写顺序不同而误判
      expect([...(route.meta?.roles as Role[])].sort(), `${route.path} 的 meta.roles 偏离 SSOT`).toEqual(
        [...expected].sort(),
      )
    }
  })

  it('3. 25 格判定矩阵与 SSOT §3.2 完全一致', () => {
    const actual: Record<string, string[]> = {}
    for (const role of ROLES) {
      for (const [path, allowed] of Object.entries(MATRIX)) {
        const route = MODULE_ROUTES.find((r) => r.path === path)
        const roles = (route?.meta?.roles as Role[]) ?? []
        const cell = roles.includes(role) ? '可见' : '403'
        const expectCell = allowed.includes(role) ? '可见' : '403'
        expect(cell, `[${role}] ${path}`).toBe(expectCell)
        actual[role] = [...(actual[role] ?? []), `${path}=${cell}`]
      }
    }
    // 25 格全覆盖（5 角色 × 5 模块）
    expect(Object.keys(actual)).toHaveLength(5)
    for (const role of ROLES) expect(actual[role]).toHaveLength(5)
  })

  it('4. app/router 实际装配的路由与模块导出一致（防装配漏项）', () => {
    const root = router.options.routes.find((r: RouteRecordRaw) => r.path === '/')
    expect(root, '缺少根路由 /').toBeDefined()
    const children = (root!.children ?? []) as RouteRecordRaw[]
    const assembled = children.filter((r: RouteRecordRaw) => r.path !== '403' && r.path !== 'login')

    for (const route of MODULE_ROUTES) {
      const hit = assembled.find((r: RouteRecordRaw) => r.path === route.path)
      expect(hit, `路由未装配：${route.path}`).toBeDefined()
      expect([...(hit!.meta?.roles as Role[])].sort(), `装配侧 meta.roles 与模块不一致：${route.path}`).toEqual(
        [...(route.meta?.roles as Role[])].sort(),
      )
      expect(hit!.meta?.title).toBe(route.meta?.title)
    }
  })

  it('5. 登录页与 403 页为顶层路由（不在 AdminLayout 内）', () => {
    const top = router.options.routes.map((r: RouteRecordRaw) => r.path)
    expect(top).toContain('/login')
    expect(top).toContain('/403')
    const root = router.options.routes.find((r: RouteRecordRaw) => r.path === '/')
    const childPaths = ((root!.children ?? []) as RouteRecordRaw[]).map((r: RouteRecordRaw) => r.path)
    expect(childPaths).not.toContain('/login')
    expect(childPaths).not.toContain('/403')
  })
})
