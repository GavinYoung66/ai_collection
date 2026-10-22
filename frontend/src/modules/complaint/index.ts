import { routes } from './routes'

/**
 * complaint 模块入口（BizModule 契约，SSOT §7.1 / D2）。
 * 唯一出口：routes；模块内自包含，不向外暴露内部文件。
 */
export const BizModule = {
  name: 'complaint' as const,
  routes,
}
