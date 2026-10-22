import type { Role } from '@/shared/types/common'
import 'vue-router'

/**
 * 路由 meta 类型增强（SSOT §11 / D4）。
 * meta.title 驱动菜单与面包屑；meta.icon 为 @element-plus/icons-vue 组件名；
 * meta.roles 声明可见角色（缺省 = 登录即可见）。
 */
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    icon?: string
    roles?: Role[]
    keepAlive?: boolean
  }
}
