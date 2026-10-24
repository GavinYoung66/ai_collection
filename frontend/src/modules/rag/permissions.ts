/**
 * rag 模块业务权限（SSOT §19.6：共享层只提供注入桥，业务 allowlist 留在模块）。
 */
import type { ComputedRef } from 'vue'
import { useHasAnyRole } from '@/shared'

/** 写操作按钮级 allowlist（后端词表 ADMIN/SUPERVISOR，SSOT §5.0 F；
 *  覆盖上传/删除/重建索引/摘要编辑/词库刷新；ai_trainer 不在列 → 置灰） */
export const RAG_WRITE_ROLES = ['admin', 'supervisor'] as const

/** 置灰悬浮说明（spec 原文逐字一致，勿改文案） */
export const RAG_WRITE_FORBIDDEN_TIP = '该操作需 admin/supervisor 权限'

/** 当前角色是否可执行写操作（响应式）；null/越权角色一律 false（fail-closed） */
export function useKbWriteAllowed(): ComputedRef<boolean> {
  return useHasAnyRole(RAG_WRITE_ROLES)
}
