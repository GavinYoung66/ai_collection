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

/** 递归展平嵌套路由，拼完整 path（嵌套路由 path 自动拼接父级前缀） */
function flattenRoutes(routes: RouteRecordRaw[], parentPath = ''): RouteRecordRaw[] {
  const result: RouteRecordRaw[] = []
  for (const r of routes) {
    const fullPath = r.path.startsWith('/')
      ? r.path
      : `${parentPath}/${r.path ?? ''}`.replace(/\/+$/, '')
    if (r.children && r.children.length > 0) {
      // 只展平有 meta（实际菜单入口/页面路由）的子级，跳过无意义的占位路由
      result.push(...flattenRoutes(r.children, fullPath))
    } else {
      result.push({ ...r, path: fullPath })
    }
  }
  return result
}

/** SSOT §3.2 角色矩阵（唯一事实源，逐格手抄，改动必须同步此处）
 * complaint 模块嵌套后：顶层 1 条父级（path=/complaints）+ children 3 条 */
const MATRIX: Record<string, Role[]> = {
  '/complaints': ['admin', 'supervisor', 'agent'],
  '/complaints/review': ['admin', 'supervisor', 'agent'],
  '/complaints/:id': ['admin', 'supervisor', 'agent'],
  '/qc/tasks': ['admin', 'supervisor', 'qc_reviewer'],
  '/kb/documents': ['admin', 'ai_trainer'],
  '/usage': ['admin', 'supervisor'],
  '/system/users': ['admin'],
}

const ROLES: Role[] = ['admin', 'supervisor', 'agent', 'qc_reviewer', 'ai_trainer']

/** 五模块导出路由 → 展平后按完整 path 索引 */
const MODULE_ROUTES: RouteRecordRaw[] = flattenRoutes([
  ...complaintRoutes,
  ...qcRoutes,
  ...ragRoutes,
  ...usageRoutes,
  ...systemRoutes,
])

describe('角色矩阵（5 模块 × 5 角色 = 25 格 + complaint 子路由 1 行 = 26 断言）', () => {
  it('1. 各模块导出路由数量：complaint=3（嵌套结构顶层 1 条父级 + children 3 条）、其余模块 F0 各 1 条', () => {
    // complaint 模块顶层是 1 条父级路由
    expect(complaintRoutes).toHaveLength(1)
    expect(complaintRoutes[0]!.children).toHaveLength(3)
    expect(qcRoutes).toHaveLength(1)
    expect(ragRoutes).toHaveLength(1)
    expect(usageRoutes).toHaveLength(1)
    expect(systemRoutes).toHaveLength(1)
    // 展平后完整路径与 MATRIX key 对齐
    expect(MODULE_ROUTES.map((r) => r.path).sort()).toEqual(Object.keys(MATRIX).sort())
  })

  it('2. 每条路由的 meta.title 非空、meta.roles 与 SSOT §3.2 逐格一致', () => {
    for (const route of MODULE_ROUTES) {
      const expected = MATRIX[route.path]
      expect(expected, `未在 SSOT 矩阵中登记的路径：${route.path}`).toBeDefined()
      expect(route.meta?.title, `${route.path} 缺 meta.title`).toBeTruthy()
      expect([...(route.meta?.roles as Role[])].sort(), `${route.path} 的 meta.roles 偏离 SSOT`).toEqual(
        [...expected].sort(),
      )
    }
  })

  it('3. 30 格判定矩阵与 SSOT 完全一致（5 角色 × 6 路径，新增 /complaints/review）', () => {
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
    const pathCount = Object.keys(MATRIX).length
    expect(Object.keys(actual)).toHaveLength(5)
    for (const role of ROLES) expect(actual[role]).toHaveLength(pathCount)
  })

  it('4. app/router 实际装配的路由与模块导出一致（防装配漏项）', () => {
    const root = router.options.routes.find((r: RouteRecordRaw) => r.path === '/')
    expect(root, '缺少根路由 /').toBeDefined()
    // 展平 root.children（嵌套 complaint 父级也需要展平）
    const assembled = flattenRoutes(
      (root!.children ?? []) as RouteRecordRaw[],
    ).filter((r: RouteRecordRaw) => r.path !== '/login' && r.path !== '/403')

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
