import { routes } from './routes'

/**
 * rag 模块入口（BizModule 契约，SSOT §7.1 / D2）。
 */
export const BizModule = {
  name: 'rag' as const,
  routes,
}
