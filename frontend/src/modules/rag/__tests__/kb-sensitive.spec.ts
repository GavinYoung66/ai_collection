/**
 * KbSensitive 敏感词词典单测（f2-rag-console 批次⑥ / tasks 8.5）。
 *
 * 照 qc/complaint 先例：vi.mock('../api') + vi.mock('element-plus')
 * （ElMessage/ElMessageBox）；角色经 inject 桥 provide 裸角色字符串；
 * el-tooltip stub 透传 content 供悬浮说明断言。
 *
 * 覆盖点（spec「敏感词页必须只读展示词典并提供刷新」+ tasks 7.1）：
 * - current.dict text/plain 等宽纯文本只读渲染（pre 元素）；
 * - 404（拦截器映射为空串）→ 空态「词典暂不可用」，非错误弹窗；
 * - 刷新二次确认（取消不发请求）→ 展示返回的新 version 并重拉词典；
 * - ai_trainer 刷新置灰 + 悬浮说明「该操作需 admin/supervisor 权限」，点击不发请求。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  getSensitiveWordsDict: vi.fn(),
  refreshSensitiveWords: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

import KbSensitive from '../views/KbSensitive.vue'
import { CURRENT_ROLE_INJECT_KEY } from '@/shared'

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

function mountSensitive(role = 'admin') {
  return mount(KbSensitive, {
    global: {
      provide: { [CURRENT_ROLE_INJECT_KEY]: role },
      stubs: {
        'el-tooltip': {
          props: ['content', 'disabled', 'placement'],
          template:
            '<span class="ep-tooltip" :data-content="content" :data-disabled="String(disabled)"><slot /></span>',
        },
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  epMocks.ElMessage.success = vi.fn()
  epMocks.ElMessage.error = vi.fn()
  epMocks.ElMessage.warning = vi.fn()
  epMocks.ElMessage.info = vi.fn()
  epMocks.ElMessageBox.confirm = vi.fn()
  apiMocks.getSensitiveWordsDict.mockResolvedValue('暴力催收\n上门威胁\n泄露隐私\n冒充公检法\n')
})

describe('modules/rag/KbSensitive', () => {
  it('进入页面拉取 current.dict 并以等宽纯文本只读渲染', async () => {
    const wrapper = mountSensitive()
    await flushPromises()

    expect(apiMocks.getSensitiveWordsDict).toHaveBeenCalledTimes(1)
    const pre = wrapper.find('pre.kbt-dict')
    expect(pre.exists()).toBe(true)
    expect(pre.text()).toContain('暴力催收')
    expect(pre.text()).toContain('冒充公检法')
  })

  it('404（词典缺失，拦截器映射为空串）→ 空态「词典暂不可用」而非错误弹窗', async () => {
    apiMocks.getSensitiveWordsDict.mockResolvedValue('')
    const wrapper = mountSensitive()
    await flushPromises()

    expect(wrapper.text()).toContain('词典暂不可用')
    expect(wrapper.find('pre.kbt-dict').exists()).toBe(false)
    expect(epMocks.ElMessage.error).not.toHaveBeenCalled()
  })

  it('刷新词库：二次确认后展示新 version 并重拉词典', async () => {
    const wrapper = mountSensitive('admin')
    await flushPromises()

    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.refreshSensitiveWords.mockResolvedValue({ version: 7 })
    apiMocks.getSensitiveWordsDict.mockClear()
    await findButtonByText(wrapper, '刷新词库')!.trigger('click')
    await flushPromises()

    expect(epMocks.ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(apiMocks.refreshSensitiveWords).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('词库版本：v7')
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('v7'))
    expect(apiMocks.getSensitiveWordsDict).toHaveBeenCalledTimes(1) // 刷新后重拉词典
  })

  it('刷新取消（二次确认取消）→ 不发起请求', async () => {
    const wrapper = mountSensitive('admin')
    await flushPromises()

    epMocks.ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await findButtonByText(wrapper, '刷新词库')!.trigger('click')
    await flushPromises()
    expect(apiMocks.refreshSensitiveWords).not.toHaveBeenCalled()
  })

  it('ai_trainer：刷新按钮置灰 + 悬浮说明，点击不发起请求；复制/加载不受影响', async () => {
    const wrapper = mountSensitive('ai_trainer')
    await flushPromises()

    const refreshBtn = findButtonByText(wrapper, '刷新词库')!
    expect(refreshBtn.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ep-tooltip').attributes('data-content')).toBe(
      '该操作需 admin/supervisor 权限',
    )
    await refreshBtn.trigger('click')
    await flushPromises()
    expect(epMocks.ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(apiMocks.refreshSensitiveWords).not.toHaveBeenCalled()

    // 浏览功能正常：词典已渲染
    expect(wrapper.find('pre.kbt-dict').exists()).toBe(true)
  })

  it.each(['admin', 'supervisor'])('角色 %s：刷新按钮可用', async (role) => {
    const wrapper = mountSensitive(role)
    await flushPromises()
    const refreshBtn = findButtonByText(wrapper, '刷新词库')!
    expect(refreshBtn.attributes('disabled')).toBeUndefined()
  })
})
