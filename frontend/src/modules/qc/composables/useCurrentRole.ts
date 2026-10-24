/**
 * 当前登录角色 inject 桥（f2-qc-console 批次4）。
 *
 * 背景：§7 铁律 + depcruise `no-modules-to-app` 禁止 modules/ import app/stores/auth，
 * 而按钮级权限（spec §5.0 F，如详情人审按钮组对 admin 置灰）又需要当前角色。
 * 桥接约定：app 层 AdminLayout 以字符串 key 'app:currentRole' provide 一个
 * ComputedRef<Role | null>；本 composable 是模块侧唯一读取点。
 * 为降低测试接线成本，provide 值同时兼容裸 Role 字符串；未 provide 时回退 null
 * （按无角色处理——按钮一律置灰，与守卫「role 为空 → 403」同向收紧）。
 */
import { computed, inject, type ComputedRef } from 'vue'
import type { Role } from '@/shared/types/common'

/** provide/inject 契约 key（AdminLayout 与本文件两侧保持一致，勿改名） */
export const CURRENT_ROLE_INJECT_KEY = 'app:currentRole'

type ProvidedRole = ComputedRef<Role | null> | Role | null

/** 读取当前角色（响应式）；未 provide 返回 null */
export function useCurrentRole(): ComputedRef<Role | null> {
  const provided = inject<ProvidedRole>(CURRENT_ROLE_INJECT_KEY, null)
  return computed(() => {
    // AdminLayout provide 的是 ComputedRef；测试可直接 provide 裸角色字符串
    if (provided && typeof provided === 'object' && 'value' in provided) {
      return provided.value
    }
    return provided ?? null
  })
}
