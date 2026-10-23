<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/app/stores/auth'
import {
  ChatDotRound,
  CircleCheck,
  Reading,
  DataAnalysis,
  Setting,
  Fold,
  Expand,
  SwitchButton,
  Warning,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'

// 主布局（SSOT §11 / D11）：侧边栏 + 顶栏 + 面包屑 + router-view。
// 布局不 import 任何 modules/*，菜单由路由表 + 当前角色过滤生成。
// 菜单渲染同时支持两种路由结构：
//   1. 扁平路由（单条 el-menu-item，如 qc/rag/usage/system）
//   2. 嵌套路由（父级 el-sub-menu + 子级 el-menu-item，如 complaint）
// 子路由 meta.menu === false 时不进入侧边栏（详情页是导航目标，不是菜单入口）
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

// 侧边栏折叠状态
const collapsed = ref(false)

// 图标映射：route.meta.icon（字符串键）→ element-plus 图标组件
const ICON_MAP: Record<string, Component> = {
  ChatDotRound,
  CircleCheck,
  Reading,
  DataAnalysis,
  Setting,
  Warning,
}

// 菜单项统一结构：group = 父级路由，children = 侧边栏实际渲染项
interface MenuGroup {
  path: string
  title: string
  icon?: string
  roles: string[]
  children: MenuItem[]
}

interface MenuItem {
  path: string        // 完整路径（如 /complaints/review）
  title: string
  icon?: string
  menu: boolean       // true = 侧边栏显示，false = 隐藏（如详情页）
}

// 从路由表提取 AdminLayout 下的业务子路由，按当前角色过滤生成菜单
const menuRoutes = computed<MenuGroup[]>(() => {
  const root = router.options.routes.find((r) => r.path === '/')
  const children = root?.children ?? []

  function filterByRoles(roles: string[] | undefined): boolean {
    if (!roles || roles.length === 0) return true
    return auth.role ? roles.includes(auth.role) : false
  }

  return children
    .filter((r) => filterByRoles(r.meta?.roles as string[] | undefined))
    .map((r): MenuGroup => {
      // 有嵌套 children → 生成 group + 过滤子级
      if (r.children && r.children.length > 0) {
        const visibleChildren: MenuItem[] = r.children
          .filter((c) => filterByRoles(c.meta?.roles as string[] | undefined))
          .filter((c) => c.meta?.menu !== false)
          .map((c): MenuItem => {
            const childPath = (c.path ?? '').startsWith('/')
              ? (c.path as string)
              : `${r.path}/${c.path ?? ''}`.replace(/\/+$/, '') // 去掉末尾多余 /
            return {
              path: childPath,
              title: (c.meta?.title as string) ?? '',
              icon: c.meta?.icon as string | undefined,
              menu: c.meta?.menu !== false,
            }
          })

        return {
          path: r.path,
          title: (r.meta?.title as string) ?? '',
          icon: r.meta?.icon as string | undefined,
          roles: (r.meta?.roles as string[] | undefined) ?? [],
          children: visibleChildren,
        }
      }

      // 扁平单路由 → 转成单子级 group
      return {
        path: r.path,
        title: (r.meta?.title as string) ?? '',
        icon: r.meta?.icon as string | undefined,
        roles: (r.meta?.roles as string[] | undefined) ?? [],
        children: [{
          path: r.path,
          title: (r.meta?.title as string) ?? '',
          icon: r.meta?.icon as string | undefined,
          menu: true,
        }],
      }
    })
})

// 面包屑：由当前路由 matched 的 meta.title 链生成
const breadcrumbs = computed(() =>
  route.matched
    .filter((r) => r.meta?.title)
    .map((r) => ({ title: r.meta!.title!, path: r.path })),
)

function toggleCollapse(): void {
  collapsed.value = !collapsed.value
}

// 父级菜单整行点击（含 padding 区域，箭头除外）→ 跳转到第一个可见子路由（如「投诉研判」→ /complaints）
// el-sub-menu 默认只展开/折叠不跳转；事件委托在 el-menu 捕获阶段执行，与展开/折叠不冲突
// 折叠状态下点击父级图标是弹 popup 选子项，不跳转
function handleMenuCapture(e: MouseEvent): void {
  if (collapsed.value) return
  const el = e.target as HTMLElement
  if (el.closest('.el-sub-menu__icon-arrow')) return // 点击箭头只展开/折叠
  const titleEl = el.closest('.el-sub-menu__title')
  if (!titleEl) return // 普通 el-menu-item 点击交给 el-menu router 模式处理
  const groupPath = titleEl.closest('[data-group-path]')?.getAttribute('data-group-path')
  const group = menuRoutes.value.find((g) => g.path === groupPath)
  if (!group || group.children.length === 0) return
  const target = group.children[0].path
  if (route.path !== target) {
    router.push(target)
  }
}

function handleLogout(): void {
  auth.logout()
}
</script>

<template>
  <el-container class="admin-layout">
    <!-- 左侧可折叠侧边栏 -->
    <el-aside
      :width="collapsed ? '64px' : '220px'"
      class="aside"
    >
      <div class="logo">
        <span v-if="!collapsed">智能催收 AI 平台</span>
        <span v-else>AI</span>
      </div>
      <el-menu
        :default-active="route.path"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        background-color="#001529"
        text-color="#b7bdc8"
        active-text-color="#ffffff"
        @click.capture="handleMenuCapture"
      >
        <template v-for="group in menuRoutes" :key="group.path">
          <!-- 多个可见子级 → el-sub-menu（可折叠）
               index 加 #group 后缀：与子级 el-menu-item 的 index（路由路径）区分，
               否则父级 index=/complaints 与「投诉列表」子项 index=/complaints 冲突（重复 key 警告） -->
          <el-sub-menu
            v-if="group.children.length > 1"
            :index="`${group.path}#group`"
            :data-group-path="group.path"
          >
            <template #title>
              <el-icon>
                <component :is="ICON_MAP[group.icon ?? '']" />
              </el-icon>
              <span>{{ group.title }}</span>
            </template>
            <el-menu-item
              v-for="child in group.children"
              :key="child.path"
              :index="child.path"
            >
              <template #title>
                {{ child.title }}
              </template>
            </el-menu-item>
          </el-sub-menu>

          <!-- 单条（扁平）→ el-menu-item -->
          <el-menu-item
            v-else
            :index="group.children[0]?.path ?? group.path"
          >
            <el-icon>
              <component :is="ICON_MAP[group.icon ?? '']" />
            </el-icon>
            <template #title>
              {{ group.title }}
            </template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶栏：折叠按钮 + 面包屑 + 用户信息 + 登出 -->
      <el-header class="header">
        <div class="header-left">
          <el-icon
            class="collapse-btn"
            @click="toggleCollapse"
          >
            <Fold v-if="!collapsed" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <!-- key 用 path+idx：嵌套路由下父级与 index 子级 record.path 可能相同（如 /complaints），会触发重复 key 警告 -->
            <el-breadcrumb-item
              v-for="(b, idx) in breadcrumbs"
              :key="`${b.path}-${idx}`"
              :to="idx === breadcrumbs.length - 1 ? undefined : b.path"
            >
              {{ b.title }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <span class="user-name">{{ auth.user?.username }}</span>
          <el-tag
            size="small"
            type="info"
          >
            {{ auth.role }}
          </el-tag>
          <el-button
            text
            @click="handleLogout"
          >
            <el-icon><SwitchButton /></el-icon>
            登出
          </el-button>
        </div>
      </el-header>

      <!-- 中央内容区 -->
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-layout {
  height: 100vh;
}
.aside {
  background-color: #001529;
  transition: width 0.2s;
  overflow: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid #1f2d3d;
}
/* el-sub-menu 标题行点击跳转由 el-menu 捕获阶段委托处理（见 handleMenuCapture） */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
  padding: 0 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.collapse-btn {
  font-size: 20px;
  cursor: pointer;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.user-name {
  font-size: 14px;
  color: #303133;
}
.main {
  background: #f5f7fa;
  padding: 20px;
}
</style>
