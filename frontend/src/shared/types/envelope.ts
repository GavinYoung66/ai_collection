/**
 * 统一响应信封（SSOT §5.0.1）：所有后端接口的外壳。
 * code 兼容 number 与 string（收敛期 D1）；data 为业务负载。
 */
export interface ApiEnvelope<T> {
  code: number | string
  message: string
  data: T
  trace_id: string
}

/**
 * 分页结果（与后端 PageResponse 逐字段一致，SSOT §5.0.1）。
 * 字段名保持 snake_case，禁止 camelCase 转换。
 */
export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/**
 * 成功判定：code 为 0 或 "0" 即视为成功（兼容 D1，收敛后改为单值）。
 */
export function isSuccess(code: number | string): boolean {
  return code === 0 || code === '0'
}
