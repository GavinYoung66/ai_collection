/**
 * 持久化键常量（SSOT §10）。
 * 仅 auth store 读写 localStorage，业务代码不得散写 key 字符串。
 * 禁止建立统一 storage 管理抽象（SSOT §18 明确不做）。
 */
export const STORAGE_KEYS = {
  TOKEN: 'ac_token',
} as const
