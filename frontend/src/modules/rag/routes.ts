import type { RouteRecordRaw } from 'vue-router'
import ModulePlaceholder from '@/shared/components/base/ModulePlaceholder.vue'

/**
 * RAG 知识库模块路由（F0 仅占位，F2 扩展子路由）。
 * meta.roles 与 SSOT §3.2 矩阵逐格一致：admin / ai_trainer。
 */
export const routes: RouteRecordRaw[] = [
  {
    path: '/kb/documents',
    name: 'KbDocuments',
    component: ModulePlaceholder,
    props: { title: 'RAG 知识库' },
    meta: { title: 'RAG 知识库', icon: 'Reading', roles: ['admin', 'ai_trainer'] },
  },
]
