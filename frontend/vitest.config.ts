import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vitest 配置：复用 vite 的 alias 与 Vue 插件，测试环境用 jsdom（提供 window）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 编译期变量替换：template 里的 import.meta.env.DEV 在 vitest CommonJS 模式下解析失败，
  // 这里把整个 import.meta.env 对象替换成静态值（DEV=true 让演示门控在测试中可见）
  define: {
    'import.meta.env': JSON.stringify({
      DEV: true,
      PROD: false,
      MODE: 'test',
      BASE_URL: '/',
      VITE_DEV_DEMO: '',
    }),
  },
  test: {
    environment: 'jsdom',
  },
})
