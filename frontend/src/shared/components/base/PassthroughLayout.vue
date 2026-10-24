<!--
  PassthroughLayout：仅渲染 <router-view /> 的占位布局组件。
  用于嵌套路由结构的父级容器——把布局工作交给子路由自身（如 AdminLayout）。
  仅用于模块嵌套路由的父级 component，不在菜单侧边栏出现。
-->
<template>
  <router-view />
</template>
