/**
 * 登录页组件级回归（tasks 5.1 / 5.3 / D5）。
 *
 * 把批次 5 的人工实测固化为可回归断言：
 *   ① 提交中二次触发只发 1 次登录请求（100ms 连点保护）
 *   ② B0002 → 「用户名或密码错误」+ trace 后 8 位，且用户名不清空、不跳转
 *   ③ B0003 → 「账号已停用或锁定」，不触发清态、不跳转
 *   ④ 成功：redirect 合法回跳；否则按角色落点
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ElCard, ElForm, ElFormItem, ElInput, ElButton } from 'element-plus'
import type { RequestError } from '@/shared/request'

/** 受控的假 auth store：login 由测试逐例接管 */
const fakeAuth = vi.hoisted(() => ({
  role: 'admin' as string | null,
  login: vi.fn(),
}))

/** 当前 query 由测试逐例设置 */
const routeState = vi.hoisted(() => ({ query: {} as Record<string, string> }))
const pushSpy = vi.hoisted(() => vi.fn())

vi.mock('@/app/stores/auth', () => ({ useAuthStore: () => fakeAuth }))
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRouter: () => ({ push: pushSpy }),
    useRoute: () => routeState,
  }
})

// 必须在 mock 之后导入组件
import Login from '@/app/pages/Login.vue'

/** 挂载登录页，返回 wrapper 与便捷填充方法 */
function mountLogin() {
  const wrapper = mount(Login, {
    global: { components: { ElCard, ElForm, ElFormItem, ElInput, ElButton } },
  })
  const inputs = wrapper.findAll('input')
  return {
    wrapper,
    setCredential: async (username = 'admin', password = 'Admin@2026') => {
      await inputs[0].setValue(username)
      await inputs[1].setValue(password)
    },
    submit: () => wrapper.find('form').trigger('submit'),
    errorTip: () => wrapper.find('.error-tip').text(),
  }
}

/** 构造带 code / trace 的业务错误（与 request.ts 的 makeError 同形） */
function bizError(message: string, code: string, trace: string): RequestError {
  return Object.assign(new Error(message), { code, trace })
}

describe('登录页 Login.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fakeAuth.role = 'admin'
    routeState.query = {}
  })

  it('1. 提交中再次触发只发 1 次登录请求（连点保护）', async () => {
    // 挂起 login，制造「提交中」窗口
    let release!: () => void
    fakeAuth.login.mockImplementation(
      () => new Promise<void>((resolve) => {
        release = resolve
      }),
    )

    const ui = mountLogin()
    await ui.setCredential()

    // 同一 tick 内连续触发两次提交（模拟连点，绕过按钮禁用，直接验守卫）
    void ui.submit()
    void ui.submit()
    await flushPromises()

    expect(fakeAuth.login).toHaveBeenCalledTimes(1)
    expect(fakeAuth.login).toHaveBeenCalledWith('admin', 'Admin@2026')

    release()
    await flushPromises()
  })

  it('2. 错误密码（B0002）原地提示 + trace 后 8 位，且用户名不清空、不跳转', async () => {
    fakeAuth.login.mockRejectedValue(
      bizError('用户名或密码错误', 'B0002', 'tr_9faeba8712345678'),
    )

    const ui = mountLogin()
    await ui.setCredential('admin', 'wrong-pass')
    await ui.submit()
    await flushPromises()

    expect(ui.errorTip()).toBe('用户名或密码错误（trace: 12345678）')
    // 用户名保留，便于重试
    expect((ui.wrapper.findAll('input')[0].element as HTMLInputElement).value).toBe('admin')
    // 失败不跳页
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('3. 停用/锁定账号（B0003）原地提示，不清态、不跳转', async () => {
    fakeAuth.login.mockRejectedValue(
      bizError('账号已停用或锁定', 'B0003', 'tr_78333909aabbccdd'),
    )

    const ui = mountLogin()
    await ui.setCredential()
    await ui.submit()
    await flushPromises()

    expect(ui.errorTip()).toBe('账号已停用或锁定（trace: aabbccdd）')
    // B0003 不得当作登录态失效：不清态（未调用 clearAuth 语义的 login 之外的副作用）、不跳转
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('4. 未知错误码提示后端原文', async () => {
    fakeAuth.login.mockRejectedValue(bizError('网关错误', 'B0008', 'tr_11112222'))

    const ui = mountLogin()
    await ui.setCredential()
    await ui.submit()
    await flushPromises()

    expect(ui.errorTip()).toBe('网关错误')
  })

  it('5. 成功且 redirect 合法 → 回跳该路径', async () => {
    fakeAuth.login.mockResolvedValue(undefined)
    routeState.query = { redirect: '/qc/tasks' }

    const ui = mountLogin()
    await ui.setCredential()
    await ui.submit()
    await flushPromises()

    expect(pushSpy).toHaveBeenCalledWith('/qc/tasks')
  })

  it('6. 成功但 redirect 非法 → 按角色落点（不信任外部跳转）', async () => {
    fakeAuth.login.mockResolvedValue(undefined)
    fakeAuth.role = 'ai_trainer'
    routeState.query = { redirect: 'https://evil.com' }

    const ui = mountLogin()
    await ui.setCredential()
    await ui.submit()
    await flushPromises()

    expect(pushSpy).toHaveBeenCalledWith('/kb/documents')
  })

  it('7. 用户名为空 → 本地校验提示，不发请求', async () => {
    const ui = mountLogin()
    await ui.setCredential('', '')
    await ui.submit()
    await flushPromises()

    expect(fakeAuth.login).not.toHaveBeenCalled()
    expect(ui.errorTip()).toBe('请输入用户名和密码')
  })

  it('8. 密码输入框为掩码类型', () => {
    const ui = mountLogin()
    const passwordInput = ui.wrapper.findAll('input')[1].element as HTMLInputElement
    expect(passwordInput.type).toBe('password')
  })
})
