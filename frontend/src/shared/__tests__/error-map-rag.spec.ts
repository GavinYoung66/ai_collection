/**
 * error-map RAG 白名单零新增断言（f2-rag-console 批次⑥ / tasks 8.6，§15.1）。
 *
 * 与既有 error-map.spec.ts 同目录新增（既有实现与既有断言零改动）：
 * rag 模块（F2）未新增任何错误码映射——A3 段仅 A3004 有白名单映射（F0 既有），
 * A3001/A3002 等 rag 业务码一律走默认分支：message 直出（缺失时通用文案），
 * 由拦截器附 trace 后 8 位。
 */
import { describe, it, expect } from 'vitest'
import { getErrorMessage } from '@/shared/utils/error-map'

describe('error-map RAG 白名单零新增（f2-rag-console §15.1）', () => {
  // A3001（不支持的上传格式）/A3002（文档过大）走默认分支：后端 message 原文直出
  it.each(['A3001', 'A3002'])('%s 未列入白名单：默认分支 message 直出', (code) => {
    expect(getErrorMessage(code, '不支持的文件格式')).toBe('不支持的文件格式')
  })

  it('A3001/A3002 message 缺失时返回通用文案', () => {
    expect(getErrorMessage('A3001')).toBe('请求失败')
    expect(getErrorMessage('A3002')).toBe('请求失败')
  })

  // A3 段其余邻近码同样未列入（佐证 rag 未新增映射）
  it.each(['A3003', 'A3005', 'A3006'])('%s 未列入白名单：默认分支', (code) => {
    expect(getErrorMessage(code, '后端原文')).toBe('后端原文')
    expect(getErrorMessage(code)).toBe('请求失败')
  })

  // A3 段唯一白名单项仍是 F0 既有的 A3004（rag 批次未改动该映射）
  it('A3004 保持既有映射不变', () => {
    expect(getErrorMessage('A3004')).toBe('检索相关性不足，未找到相关内容')
  })
})
