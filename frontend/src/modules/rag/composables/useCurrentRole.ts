/**
 * 当前登录角色 inject 桥（f2-rag-console 批次⑥ / tasks 7.1）。
 *
 * 与 modules/qc/composables/useCurrentRole.ts 等价实现（§7 铁律 + depcruise
 * `no-cross-module-import`/`no-modules-to-app` 禁止跨模块复用，模块内自写）：
 * - 背景：模块不得 import app/stores/auth，而按钮级权限（spec「按钮级权限必须按后端
 *   allowlist 对 ai_trainer 置灰」，§5.0 F）又需要当前角色；
 * - 桥接约定：app 层 AdminLayout 以字符串 key 'app:currentRole' provide 一个
 *   ComputedRef<Role | null>；本 composable 是 rag 模块侧唯一读取点；
 * - 为降低测试接线成本，provide 值同时兼容裸 Role 字符串；未 provide 时回退 null
 *   （按无角色处理——写操作一律置灰，与守卫「role 为空 → 403」同向收紧）。
 */
import { computed, inject, type ComputedRef } from 'vue'
import type { Role } from '@/shared/types/common'

/** provide/inject 契约 key（AdminLayout 与本文件两侧保持一致，勿改名） */
export const CURRENT_ROLE_INJECT_KEY = 'app:currentRole'

/** 写操作按钮级 allowlist（后端词表 ADMIN/SUPERVISOR，SSOT §5.0 F；
 *  覆盖上传/删除/重建索引/摘要编辑/词库刷新；ai_trainer 不在列 → 置灰） */
export const RAG_WRITE_ROLES = ['admin', 'supervisor'] as const

/** 置灰悬浮说明（spec 原文逐字一致，勿改文案） */
export const RAG_WRITE_FORBIDDEN_TIP = '该操作需 admin/supervisor 权限'

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

/** 当前角色是否可执行写操作（响应式）；null/越权角色一律 false（fail-closed） */
export function useKbWriteAllowed(): ComputedRef<boolean> {
  const role = useCurrentRole()
  return computed(
    () => !!role.value && (RAG_WRITE_ROLES as readonly string[]).includes(role.value),
  )
}
