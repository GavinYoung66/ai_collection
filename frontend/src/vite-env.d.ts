/// <reference types="vite/client" />

/**
 * Vite 注入的环境变量类型声明（与 tsconfig.app.json 的 types:["vite/client"] 互补）。
 * 新增环境变量时在此补全类型，避免业务代码中 import.meta.env 取到 any。
 */
interface ImportMetaEnv {
  /**
   * 后端网关前缀。F0 起约定为空串（api 路径自带 `/api/v1` 前缀），
   * 且 `.env.*` 被 gitignore 忽略，CI 干净环境取不到该变量，
   * 故类型必须是 `string | undefined`，由 request.ts 兜底为 ''。
   */
  readonly VITE_API_BASE?: string
  /**
   * 开发态 mock 开关（SSOT §16.1）：仅当 === 'true' 时 mocks/browser.ts
   * 安装请求拦截（与 main.ts 的 import.meta.env.DEV 门控构成双重门控），
   * 生产构建不含 mock。
   */
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
