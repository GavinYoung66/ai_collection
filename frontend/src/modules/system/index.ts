import { routes } from './routes'

/**
 * system 模块入口（BizModule 契约，SSOT §7.1 / D2）。
 */
export const BizModule = {
  name: 'system' as const,
  routes,
}
