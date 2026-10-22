import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type UserConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],

  // 管理台部署在子路径 /admin/ 下，所有资源与路由都以此为前缀
  base: '/admin/',

  resolve: {
    alias: {
      // 统一用 @ 指向 src，业务代码禁止相对路径深链
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    // 开发态把 /api 转发到本地后端网关，前端代码不出现 8080 硬编码
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
} satisfies UserConfig)
