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
} from '@element-plus/icons-vue'
import type { Component } from 'vue'

// 主布局（SSOT §11 / D11）：侧边栏 + 顶栏 + 面包屑 + router-view。
// 布局不 import 任何 modules/*，菜单由路由表 + 当前角色过滤生成。
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
}

// 从路由表提取 AdminLayout 下的业务子路由，按当前角色过滤生成菜单
const menuRoutes = computed(() => {
  const root = router.options.routes.find((r) => r.path === '/')
  const children = root?.children ?? []
  return children.filter((r) => {
    const roles = r.meta?.roles
    // meta.roles 缺省 = 登录即可见；否则需当前角色命中
    if (!roles || roles.length === 0) return true
    return auth.role ? roles.includes(auth.role) : false
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
      >
        <el-menu-item
          v-for="item in menuRoutes"
          :key="item.path"
          :index="item.path"
        >
          <el-icon>
            <component :is="ICON_MAP[item.meta?.icon ?? '']" />
          </el-icon>
          <template #title>
            {{ item.meta?.title }}
          </template>
        </el-menu-item>
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
            <el-breadcrumb-item
              v-for="(b, idx) in breadcrumbs"
              :key="b.path"
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
