/**
 * 唯一 ID 生成工具（无业务语义，shared/utils 可复用）。
 *
 * 两个函数解决两类不同需求，避免各模块自造：
 * - randomUuid()：UUID v4 格式，用于 X-Idempotency-Key 等需要 UUID 的地方；
 *   优先 crypto.randomUUID，非安全上下文（HTTP 非 localhost）/ 旧环境降级为
 *   Math.random 拼出的 UUID v4 格式（仍匹配 /^[0-9a-f]{8}-...$/ 形态）。
 * - randomTraceId()：32 位 hex，对齐后端 trace_id 格式（common/trace/middleware.py
 *   生成 uuid4 hex），用于 X-Trace-Id 上下文，保证与后端日志格式一致。
 */

/** UUID v4（带连字符）；crypto.randomUUID 不可用时降级为等价格式 */
export function randomUuid(): string {
  const c = globalThis.crypto as undefined | { randomUUID?: () => string }
  if (c?.randomUUID) return c.randomUUID()
  const bytes = new Uint8Array(16)
  for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'))
  return (
    `${hex[0]}${hex[1]}${hex[2]}${hex[3]}-${hex[4]}${hex[5]}-` +
    `${hex[6]}${hex[7]}-${hex[8]}${hex[9]}-${hex[10]}${hex[11]}${hex[12]}${hex[13]}${hex[14]}${hex[15]}`
  )
}

/** 32 位 hex trace id（对齐后端 uuid4 hex，无连字符） */
export function randomTraceId(): string {
  const bytes = new Uint8Array(16)
  const c = globalThis.crypto as undefined | {
    getRandomValues?: (a: Uint8Array) => Uint8Array
  }
  if (c?.getRandomValues) {
    c.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}
