import { describe, it, expect } from 'vitest'
import { getErrorMessage, formatTrace } from '@/shared/utils/error-map'

describe('error-map 错误码白名单', () => {
  // B 系统级白名单
  it.each([
    ['B0001', '参数校验失败'],
    ['B0002', '请先登录'],
    ['B0003', '无操作权限'],
    ['B0004', '请求的资源不存在'],
    ['B0005', '已提交，请勿重复操作'],
    ['B0006', '系统繁忙，稍后重试'],
    ['B0007', '系统内部错误'],
    ['B0008', '网关错误'],
    ['B0009', '服务暂不可用'],
    ['B0010', '该操作被合规策略拦截'],
  ])('%s 映射为 %s', (code, expected) => {
    expect(getErrorMessage(code)).toBe(expected)
  })

  // A2 质检
  it.each([
    ['A2002', '质检入参不合法'],
    ['A2003', '对话不可质检（通话未结束、全文未齐或通话主数据不存在）'],
  ])('%s 映射为 %s', (code, expected) => {
    expect(getErrorMessage(code)).toBe(expected)
  })

  // A3 RAG
  it('A3004 映射为检索相关性不足', () => {
    expect(getErrorMessage('A3004')).toBe('检索相关性不足，未找到相关内容')
  })

  // A5 投诉
  it.each([
    ['A5001', '投诉工单不存在'],
    ['A5002', '状态已变更'],
    ['A5014', '模式切换失败/已回滚'],
  ])('%s 映射为 %s', (code, expected) => {
    expect(getErrorMessage(code)).toBe(expected)
  })

  // A5015/A5016/A5017 必须映射为正向「已转人工审核」，不得展示为失败
  it.each(['A5015', 'A5016', 'A5017'])('%s 映射为正向「已转人工审核」', (code) => {
    const msg = getErrorMessage(code)
    expect(msg).toBe('已转人工审核')
    expect(msg).not.toContain('失败')
    expect(msg).not.toContain('错误')
  })

  // 未列入白名单的 code：走默认分支，返回后端 message 原文
  it('未列入白名单的 code 返回后端 message 原文', () => {
    expect(getErrorMessage('A1001', '非工作时段禁止外呼')).toBe('非工作时段禁止外呼')
  })

  it('未列入白名单且 message 缺失时返回通用文案', () => {
    expect(getErrorMessage('A9999')).toBe('请求失败')
  })

  // formatTrace：取后 8 位
  it('formatTrace 取 trace_id 后 8 位', () => {
    expect(formatTrace('tr_0123456789abcdef')).toBe('89abcdef')
  })

  it('formatTrace 缺失时返回「无」', () => {
    expect(formatTrace(undefined)).toBe('无')
    expect(formatTrace(null)).toBe('无')
    expect(formatTrace('')).toBe('无')
  })
})
