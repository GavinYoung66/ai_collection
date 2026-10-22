/**
 * 错误码白名单映射（SSOT §15.1，唯一来源 common/errors/codes.py）。
 * 原则：只对「需要改变 UI 行为」的错误码做特殊处理；其余一律 message 直出 + trace_id 后 8 位。
 * 前端不得新增、改名或重定义后端错误码。
 */

// 白名单：code → 用户提示文案（A5015/A5016/A5017 统一映射为正向「已转人工审核」，不得展示为失败）
const ERROR_MESSAGE_MAP: Record<string, string> = {
  // B 系统级
  B0001: '参数校验失败',
  B0002: '请先登录',
  B0003: '无操作权限',
  B0004: '请求的资源不存在',
  B0005: '已提交，请勿重复操作',
  B0006: '系统繁忙，稍后重试',
  B0007: '系统内部错误',
  B0008: '网关错误',
  B0009: '服务暂不可用',
  B0010: '该操作被合规策略拦截',
  // A2 质检
  A2002: '质检入参不合法',
  A2003: '对话不可质检（通话未结束、全文未齐或通话主数据不存在）',
  // A3 RAG
  A3004: '检索相关性不足，未找到相关内容',
  // A5 投诉
  A5001: '投诉工单不存在',
  A5002: '状态已变更',
  A5014: '模式切换失败/已回滚',
  A5015: '已转人工审核',
  A5016: '已转人工审核',
  A5017: '已转人工审核',
}

/**
 * 取错误码对应的用户提示文案。
 * - 命中白名单：返回映射文案
 * - 未命中：返回后端 message 原文（兜底），message 缺失时返回通用文案
 */
export function getErrorMessage(code: number | string, backendMessage?: string): string {
  const key = String(code)
  if (key in ERROR_MESSAGE_MAP) {
    return ERROR_MESSAGE_MAP[key]
  }
  return backendMessage || '请求失败'
}

/**
 * 格式化 trace_id 用于错误提示：取后 8 位；trace 缺失时返回「无」。
 */
export function formatTrace(traceId?: string | null): string {
  if (!traceId) return '无'
  return traceId.slice(-8)
}
