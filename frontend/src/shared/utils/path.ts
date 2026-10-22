/**
 * 路径工具：剥离 SPA 部署 base，得到「站内路由路径」。
 *
 * 背景（spec R2 Scenario / D4）：
 * 部署 base 为 `/admin/` 时，`window.location.pathname` 形如 `/admin/complaints`。
 * 若把它原样塞进 `/login?redirect=`，vue-router 的 `createWebHistory('/admin/')`
 * 会在解析时再补一次 base，最终落到 `/admin/admin/complaints` → 无匹配路由 → 白屏。
 * 因此凡是把「地址栏路径」当 redirect 用之前，必须先剥掉 base。
 */

/**
 * 从完整路径中剥离 base 前缀。
 * @param fullPath 地址栏路径（可含 search），如 `/admin/complaints?page=2`
 * @param base 部署 base，如 `/admin` 或 `/admin/`；`/` 或空串表示无 base
 * @returns 站内路由路径，如 `/complaints?page=2`；等于 base 时返回 `/`
 */
export function stripBasePath(fullPath: string, base: string): string {
  // 归一化：去掉尾部斜杠，便于统一比较
  const normalized = base.endsWith('/') ? base.slice(0, -1) : base
  if (!normalized || normalized === '/') return fullPath
  if (fullPath === normalized) return '/'
  if (fullPath.startsWith(`${normalized}/`)) return fullPath.slice(normalized.length)
  return fullPath
}
