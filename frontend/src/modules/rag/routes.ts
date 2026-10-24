/**
 * RAG 知识库模块路由（f2-rag-console 批次② / tasks 3.1，替换 F0 占位）。
 *
 * 信息架构（嵌套结构，照 complaint/qc 先例，SSOT §11.1）：
 * ├── 一级菜单：RAG 知识库（el-sub-menu，可折叠；父级 roles 为三条子路由的并集，
 * │   仅供侧边栏一级过滤；守卫读 to.meta，子级覆盖父级）
 * │   ├── 二级：文档管理    → /kb/documents        (keepAlive)
 * │   ├── 二级：检索测试台  → /kb/search           (keepAlive)
 * │   └── 二级：敏感词词典  → /kb/sensitive-words  (keepAlive)
 *
 * meta.roles 均 ['admin','ai_trainer']（SSOT §3.2 矩阵 / §11.1 路由表逐格一致；
 * 授权边界在后端——写操作按钮级对 ai_trainer 置灰按 §5.0 F 由后续批次落地）。
 * 视图一律 () => import() 懒加载（spec：进入对应路由才加载 chunk）；
 * 当前为批次②骨架视图（defineOptions name 与路由 name 逐字一致 + 占位文案），
 * 真实实现属批次④-⑥（tasks 4.x/5.x/6.x）。
 */
import type { RouteRecordRaw } from 'vue-router'
import { PassthroughLayout } from '@/shared'

export const routes: RouteRecordRaw[] = [
  {
    path: '/kb',
    name: 'KbGroup',
    component: PassthroughLayout,
    // 父级无独立页面：直访 /kb 重定向文档管理，避免「有布局、无内容」白页
    redirect: '/kb/documents',
    meta: {
      roles: ['admin', 'ai_trainer'],
      title: 'RAG 知识库',
      icon: 'Reading',
    },
    children: [
      {
        path: 'documents',
        name: 'KbDocuments',
        component: () => import('./views/KbDocuments.vue'),
        meta: {
          roles: ['admin', 'ai_trainer'],
          title: '文档管理',
          icon: 'Reading',
          keepAlive: true,
          menu: true,
        },
      },
      {
        path: 'search',
        name: 'KbSearch',
        component: () => import('./views/KbSearch.vue'),
        meta: {
          roles: ['admin', 'ai_trainer'],
          title: '检索测试台',
          icon: 'Search',
          keepAlive: true,
          menu: true,
        },
      },
      {
        path: 'sensitive-words',
        name: 'KbSensitive',
        component: () => import('./views/KbSensitive.vue'),
        meta: {
          roles: ['admin', 'ai_trainer'],
          title: '敏感词词典',
          icon: 'Lock',
          keepAlive: true,
          menu: true,
        },
      },
    ],
  },
]
