import { describe, it, expect } from 'vitest'
import { stripBasePath } from '@/shared/utils/path'

describe('shared/utils/path 剥离部署 base', () => {
  it('1. 剥离 /admin base 并保留 search', () => {
    // 回归 B2：地址栏路径含 base，直接当 redirect 会被 createWebHistory 再补一次
    expect(stripBasePath('/admin/complaints', '/admin')).toBe('/complaints')
    expect(stripBasePath('/admin/complaints?page=2', '/admin')).toBe('/complaints?page=2')
    expect(stripBasePath('/admin/qc/tasks', '/admin')).toBe('/qc/tasks')
  })

  it('2. base 带尾斜杠时同样生效（vite BASE_URL 形如 /admin/）', () => {
    expect(stripBasePath('/admin/complaints', '/admin/')).toBe('/complaints')
    expect(stripBasePath('/admin/complaints?x=1', '/admin/')).toBe('/complaints?x=1')
  })

  it('3. 路径等于 base 时返回根路径', () => {
    expect(stripBasePath('/admin', '/admin')).toBe('/')
    expect(stripBasePath('/admin/', '/admin/')).toBe('/')
  })

  it('4. 无 base（/ 或空串）时原样返回', () => {
    expect(stripBasePath('/complaints', '/')).toBe('/complaints')
    expect(stripBasePath('/complaints?page=2', '')).toBe('/complaints?page=2')
  })

  it('5. 不含 base 前缀的路径原样返回（避免误剥）', () => {
    expect(stripBasePath('/complaints', '/admin')).toBe('/complaints')
    // 形似但非前缀（/administrator 不应被 /admin 剥掉）
    expect(stripBasePath('/administrator', '/admin')).toBe('/administrator')
  })
})
