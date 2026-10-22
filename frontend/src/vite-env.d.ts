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
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
