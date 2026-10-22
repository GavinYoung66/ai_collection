import type { Router } from 'vue-router'
import type { Role } from '@/shared/types/common'
import { useAuthStore } from '@/app/stores/auth'

/**
 * 角色首页落点（SSOT §11.1 / D4）。
 * 返回该角色按矩阵可访问的第一条模块路由；无可访问路由 → /403。
 * 禁止给所有角色写死 /complaints（否则 qc_reviewer / ai_trainer 登录即 403 循环）。
 */
export function firstAccessibleRoute(role: Role | null): string {
  switch (role) {
    case 'admin':
    case 'supervisor':
    case 'agent':
      return '/complaints'
    case 'qc_reviewer':
      return '/qc/tasks'
    case 'ai_trainer':
      return '/kb/documents'
    default:
      return '/403'
  }
}

/**
 * 校验 redirect 参数：仅接受站内相对路径（以单个 / 开头且非 //）。
 * 非法则返回 false，由调用方按角色落点处理。
 */
export function isValidRedirect(path: string): boolean {
  return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//')
}

/**
 * 注册全局前置守卫（SSOT §11 / D4）。
 * 顺序严格：
 *   ① /login、/403 → 直接放行（不触发 ensureLoaded）
 *   ② await auth.ensureLoaded()（in-flight 去重）
 *   ③ 无 token → /login?redirect=<目标完整路径>
 *   ④ role 为空 → /403（不计算重定向，防递归）；role 不在 meta.roles → /403
 *   ⑤ 放行
 */
export function setupGuards(router: Router): void {
  router.beforeEach(async (to) => {
    const auth = useAuthStore()

    // ① 登录页与 403 页放行，不触发 /me 引导
    if (to.path === '/login' || to.path === '/403') {
      return true
    }

    // ② 首屏引导去重：确保 user/role 已就绪
    //    401 由请求层 unauthorizedHandler 清态跳转；500/网络异常此处兜底跳登录
    try {
      await auth.ensureLoaded()
    } catch {
      return { path: '/login', query: { redirect: to.fullPath } }
    }

    // ③ 无 token → 跳登录，带 redirect
    if (!auth.token) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }

    // ④ role 为空（token 存在但 /me 未返回有效角色）→ /403，不计算重定向防递归
    if (!auth.role) {
      return '/403'
    }

    // ④ role 不在目标路由 meta.roles → /403（meta.roles 缺省 = 登录即可见）
    const requiredRoles = to.meta?.roles as Role[] | undefined
    if (requiredRoles && requiredRoles.length > 0 && !auth.hasRole(...requiredRoles)) {
      return '/403'
    }

    // ⑤ 根路径按角色落点重定向（S4）：已登录直访 /admin/ 时根路由无子路由匹配，
    //    否则会渲染出「有布局、无内容」的白页。此处必须放在 ④ 之后，确保 role 已就绪。
    if (to.path === '/') {
      return firstAccessibleRoute(auth.role)
    }

    // ⑥ 放行
    return true
  })
}
