import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

// ESLint flat config（eslint 10）：基础推荐 + TS + Vue，再叠加本项目的分层边界规则。
export default tseslint.config(
  // 忽略产物与配置自身，避免误报
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },

  // 前端工程：浏览器环境全局变量（window / document / navigator 等）
  {
    languageOptions: {
      globals: {
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        console: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        fetch: 'readonly',
        location: 'readonly',
        history: 'readonly',
      },
    },
  },

  // JS 推荐规则集
  js.configs.recommended,

  // TypeScript 推荐规则集（含 parser 配置）
  ...tseslint.configs.recommended,

  // Vue 单文件组件推荐规则集（flat 版本）
  ...pluginVue.configs['flat/recommended'],

  // .vue 文件的 <script lang="ts"> 需用 @typescript-eslint/parser 解析，
  // 否则 defineProps<{...}>() 等 TS 泛型语法会被 espree 误判为解析错误。
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },

  // —— 分层边界：禁止跨模块深链导入 ——
  // 模块内部的 views/ 与 components/ 不得被外部直接 import，
  // 跨模块复用必须先下沉到 shared/，或经由模块 index.ts 暴露的 BizModule 入口。
  {
    files: ['**/*.{ts,tsx,vue}'],
    rules: {
      // TS 文件的未定义标识符交给 tsc 判定（DOM 类型如 MouseEvent/HTMLElement 属 lib.dom，
      // ESLint 的 no-undef 不认识类型引用，会产生误报）
      'no-undef': 'off',
      // 允许以 _ 开头的有意未使用参数（如拦截器 rejected 回调的 error）
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/modules/*/views/*', '@/modules/*/components/*'],
              message:
                '禁止跨模块深链导入 views/components；如需复用请下沉到 shared/，或通过模块 index.ts 的 BizModule 入口访问。',
            },
          ],
        },
      ],
    },
  },

  // .cjs 配置文件（如 .dependency-cruiser.cjs）跑在 Node CommonJS 下，提供全局变量
  {
    files: ['**/*.cjs'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: {
        module: 'readonly',
        require: 'readonly',
        __dirname: 'readonly',
        __filename: 'readonly',
        process: 'readonly',
      },
    },
  },
)
