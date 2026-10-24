/**
 * KbDocuments 交互单测（f2-rag-console 批次⑥ / tasks 8.3）。
 *
 * 照 modules/qc/__tests__/review.spec.ts 与 complaint/__tests__/interaction.spec.ts
 * 先例：vi.mock('../api') + vi.mock('element-plus')（ElMessage/ElMessageBox）+
 * global.stubs 承接 EP 组件（el-dialog/el-drawer/el-tooltip 渲染 slot 内容，
 * el-form.validate 走可编程 mock，el-input/el-button 转发 v-model/disabled）；
 * 角色经 inject 桥 provide 裸角色字符串（生产由 AdminLayout provide ComputedRef）。
 *
 * 覆盖点：
 * - 上传成功 → 刷新列表 + 真实 useKbIngestPolling 轮询详情至 active 终态自停
 *   （ElMessage「入库完成」）；B0001 → 对话框行内透出，不弹全局 toast；
 * - 删除/重建二次确认（取消不发请求）；重建确认框文案注明「不补全表格/图片资产」；
 *   reindex 网络层超时（无 code）→ 提示「操作已提交，请刷新列表查看 status 终态」；
 * - 摘要编辑：校验失败不发请求、成功提交、B0001 行内透出；
 *   ≤200 字 rules 以 ?raw 源码佐证（max: KB_SUMMARY_MAX_LEN）；
 * - ai_trainer：上传/删除/重建/摘要编辑按钮置灰 + 悬浮说明
 *   「该操作需 admin/supervisor 权限」，置灰点击不发起请求。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// ── Mocks（vi.mock 工厂提升，mock 函数必须经 vi.hoisted 定义）──
const apiMocks = vi.hoisted(() => ({
  listKbDocuments: vi.fn(),
  uploadKbDocument: vi.fn(),
  getKbDocument: vi.fn(),
  deleteKbDocument: vi.fn(),
  reindexKbDocument: vi.fn(),
  updateKbDocumentSummary: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

/** el-form.validate 可编程 mock（rules 真实校验依赖 EP 运行时，此处由 stub 转发） */
const formStubs = vi.hoisted(() => ({ validate: vi.fn(), clearValidate: vi.fn() }))

import KbDocuments from '../views/KbDocuments.vue'
import { KB_SUMMARY_RULES } from '../composables/kbSummaryRules'
import { KB_SUMMARY_MAX_LEN } from '../types'
import { CURRENT_ROLE_INJECT_KEY } from '@/shared'

// ── 固定数据 ──
function rowItem(over: Record<string, unknown> = {}) {
  return {
    id: 'd-1',
    title: '合规手册',
    category: 'regulation',
    status: 'active',
    chunks_count: 12,
    create_time: '2026-10-20T02:00:00Z',
    ingest_stage: 'done',
    ingest_progress: 100,
    ingest_error: null,
    ...over,
  }
}

function listResp(items = [rowItem()]) {
  return { items, total: items.length, page: 1, page_size: 20 }
}

function detailResp(over: Record<string, unknown> = {}) {
  return {
    id: 'd-1',
    title: '合规手册',
    category: 'regulation',
    content: '正文',
    status: 'active',
    version: 1,
    chunks_count: 12,
    create_time: '2026-10-20T02:00:00Z',
    update_time: '2026-10-20T02:00:00Z',
    summary: '旧摘要',
    ingest_stage: 'done',
    ingest_progress: 100,
    ingest_error: null,
    assets: [{ asset_type: 'image', page: 3, storage_path: 'p3.png', mime_type: 'image/png' }],
    ...over,
  }
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

/** EP stubs：渲染 slot 内容并转发关键 props（disabled/modelValue/error） */
function ragStubs() {
  return {
    'el-tooltip': {
      props: ['content', 'disabled', 'placement'],
      template:
        '<span class="ep-tooltip" :data-content="content" :data-disabled="String(disabled)"><slot /></span>',
    },
    'el-drawer': {
      props: ['modelValue', 'title', 'size'],
      template: '<div v-if="modelValue" class="ep-drawer"><slot /><slot name="footer" /></div>',
    },
    'el-dialog': {
      props: ['modelValue', 'title', 'width', 'closeOnClickModal'],
      template: '<div v-if="modelValue" class="ep-dialog"><slot /><slot name="footer" /></div>',
    },
    'el-form': {
      template: '<form class="ep-form"><slot /></form>',
      methods: {
        validate: (...args: unknown[]) => formStubs.validate(...args),
        clearValidate: () => formStubs.clearValidate(),
      },
    },
    'el-form-item': {
      props: ['prop', 'error', 'label'],
      template: '<div class="ep-form-item" :data-error="error || \'\'"><slot /></div>',
    },
    'el-input': {
      props: ['modelValue', 'type', 'rows', 'placeholder'],
      emits: ['update:modelValue', 'input'],
      template:
        '<textarea class="ep-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'el-button': {
      props: ['type', 'loading', 'disabled'],
      template:
        '<button class="ep-button" type="button" :disabled="disabled || loading"><slot /></button>',
    },
    'el-progress': {
      props: ['percentage', 'strokeWidth'],
      template: '<div class="ep-progress" />',
    },
    // 上传对话框整组件 stub：只保留 v-model/serverError 透出与 submit 意图
    KbUploadDialog: {
      name: 'KbUploadDialog',
      props: ['modelValue', 'submitting', 'serverError'],
      emits: ['update:modelValue', 'update:serverError', 'submit'],
      template: '<div class="kb-upload-stub" />',
    },
    StatusTag: {
      props: ['status'],
      template: '<span class="status-tag-stub">{{ status }}</span>',
    },
    TraceIdText: {
      props: ['traceId'],
      template: '<code class="trace-id-stub">{{ traceId }}</code>',
    },
  }
}

function mountDocuments(role = 'admin') {
  return mount(KbDocuments, {
    global: {
      provide: { [CURRENT_ROLE_INJECT_KEY]: role },
      stubs: ragStubs(),
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
  formStubs.validate.mockReset().mockResolvedValue(true)
  formStubs.clearValidate.mockReset()
  apiMocks.listKbDocuments.mockResolvedValue(listResp())
  apiMocks.getKbDocument.mockResolvedValue(detailResp())
})

describe('modules/rag/KbDocuments', () => {
  it('进入页面拉列表（默认 page=1/page_size=20）并渲染多状态行', async () => {
    apiMocks.listKbDocuments.mockResolvedValue(
      listResp([
        rowItem(),
        rowItem({ id: 'd-2', title: '话术中', status: 'processing', ingest_stage: 'body_index', ingest_progress: 50, chunks_count: 0 }),
        rowItem({ id: 'd-3', title: '案例集', status: 'failed', ingest_stage: null, ingest_progress: null, ingest_error: '正文抽取失败' }),
      ]),
    )
    const wrapper = mountDocuments()
    await flushPromises()

    expect(apiMocks.listKbDocuments).toHaveBeenCalledWith({ page: 1, page_size: 20 })
    const text = wrapper.text()
    expect(text).toContain('合规手册')
    expect(text).toContain('案例集')
    // processing 行展示进度条（stage→progress 映射）
    expect(wrapper.find('.ep-progress').exists()).toBe(true)
  })

  it('上传成功 → 刷新列表并轮询详情至 active 终态自停（真实 composable）', async () => {
    apiMocks.uploadKbDocument.mockResolvedValue({ doc_id: 'd-new-1', status: 'processing' })
    apiMocks.getKbDocument.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'd-new-1'
          ? detailResp({ id: 'd-new-1', title: '新文档', status: 'active' })
          : detailResp(),
      ),
    )
    const wrapper = mountDocuments('admin')
    await flushPromises()

    await findButtonByText(wrapper, '上传文档')!.trigger('click')
    const dialog = wrapper.findComponent({ name: 'KbUploadDialog' })
    expect(dialog.props('modelValue')).toBe(true)

    const file = new File(['x'], '新文档.txt')
    dialog.vm.$emit('submit', { file, category: 'regulation', summary: '' })
    await flushPromises()
    await flushPromises()

    expect(apiMocks.uploadKbDocument).toHaveBeenCalledWith(file, 'regulation', undefined)
    // 轮询详情直至 active（start 立即 poll 一次即达终态 → 自停）
    expect(apiMocks.getKbDocument).toHaveBeenCalledWith('d-new-1')
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('入库完成'))
    // 对话框关闭
    expect(wrapper.findComponent({ name: 'KbUploadDialog' }).props('modelValue')).toBe(false)
    wrapper.unmount()
  })

  it('连续上传两份文档：第二个上传不打断第一个的在途轮询（P2-4 多文档队列）', async () => {
    let uploadSeq = 0
    apiMocks.uploadKbDocument.mockImplementation(() =>
      Promise.resolve({ doc_id: `d-new-${++uploadSeq}`, status: 'processing' as const }),
    )
    // 第一份首轮仍 processing（保持轮询），第二份立即可达 active
    apiMocks.getKbDocument.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'd-new-1'
          ? detailResp({ id: 'd-new-1', title: '第一份', status: 'processing', ingest_stage: 'body_index', ingest_progress: 50 })
          : detailResp({ id, title: '第二份', status: 'active' }),
      ),
    )
    const wrapper = mountDocuments('admin')
    await flushPromises()

    await findButtonByText(wrapper, '上传文档')!.trigger('click')
    const dialog = wrapper.findComponent({ name: 'KbUploadDialog' })
    dialog.vm.$emit('submit', { file: new File(['a'], 'a.txt'), category: 'regulation', summary: '' })
    await flushPromises()
    await flushPromises()
    expect(apiMocks.getKbDocument).toHaveBeenCalledWith('d-new-1')

    // 第二份上传：应立即并发补拉（不等 3s 周期），且两份都在轮询集合内
    dialog.vm.$emit('submit', { file: new File(['b'], 'b.txt'), category: 'case', summary: '' })
    await flushPromises()
    await flushPromises()
    expect(apiMocks.getKbDocument).toHaveBeenCalledWith('d-new-2')
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('第二份'))
    wrapper.unmount()
  })

  it('上传 B0001（重名）→ 对话框行内透出，不弹全局 toast', async () => {
    apiMocks.uploadKbDocument.mockRejectedValue({ code: 'B0001', message: '同名文件正在入库中，请勿重复上传' })
    const wrapper = mountDocuments('admin')
    await flushPromises()

    await findButtonByText(wrapper, '上传文档')!.trigger('click')
    const dialog = wrapper.findComponent({ name: 'KbUploadDialog' })
    dialog.vm.$emit('submit', { file: new File(['x'], 'dup.txt'), category: 'case', summary: '' })
    await flushPromises()

    expect(dialog.props('serverError')).toBe('同名文件正在入库中，请勿重复上传')
    expect(epMocks.ElMessage.error).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('删除需二次确认：取消不发请求，确认后删除并刷新列表', async () => {
    const wrapper = mountDocuments('admin')
    await flushPromises()
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()

    const delBtn = findButtonByText(wrapper, '删除文档')!
    // 用户取消
    epMocks.ElMessageBox.confirm.mockRejectedValueOnce(new Error('cancel'))
    await delBtn.trigger('click')
    await flushPromises()
    expect(apiMocks.deleteKbDocument).not.toHaveBeenCalled()

    // 用户确认
    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.deleteKbDocument.mockResolvedValue(null)
    apiMocks.listKbDocuments.mockClear()
    await delBtn.trigger('click')
    await flushPromises()

    expect(epMocks.ElMessageBox.confirm).toHaveBeenCalled()
    expect(apiMocks.deleteKbDocument).toHaveBeenCalledWith('d-1')
    expect(apiMocks.listKbDocuments).toHaveBeenCalledTimes(1) // 删除后刷新列表
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith('文档已删除')
  })

  it('重建索引需二次确认且文案注明「不补全表格/图片资产」；确认后重建并刷新', async () => {
    const wrapper = mountDocuments('admin')
    await flushPromises()
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()

    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.reindexKbDocument.mockResolvedValue({
      doc_id: 'd-1',
      chunks_count: 12,
      status: 'active',
      assets_rebuilt: false,
    })
    await findButtonByText(wrapper, '重建索引')!.trigger('click')
    await flushPromises()

    const confirmMsg = epMocks.ElMessageBox.confirm.mock.calls[0][0] as string
    expect(confirmMsg).toContain('不补全表格/图片资产')
    expect(apiMocks.reindexKbDocument).toHaveBeenCalledWith('d-1')
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith('重建索引完成')
  })

  it('reindex 网络层超时（无 code）→ 提示引导刷新查终态，不自动重试', async () => {
    const wrapper = mountDocuments('admin')
    await flushPromises()
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()

    epMocks.ElMessageBox.confirm.mockResolvedValueOnce(undefined)
    apiMocks.reindexKbDocument.mockRejectedValue({ message: '网络异常，请稍后重试（trace: 无）' })
    await findButtonByText(wrapper, '重建索引')!.trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining('操作已提交，请刷新列表查看 status 终态'),
    )
    expect(apiMocks.reindexKbDocument).toHaveBeenCalledTimes(1) // 不自动重试
  })

  it('摘要编辑：校验失败（>200 字）行内标红不发请求；校验通过才提交', async () => {
    const wrapper = mountDocuments('admin')
    await flushPromises()
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()

    await findButtonByText(wrapper, '编辑摘要')!.trigger('click')
    const textarea = wrapper.find('.ep-dialog textarea.ep-input')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('x'.repeat(201))

    // rules 校验失败（el-form.validate reject）→ 请求不发出
    formStubs.validate.mockRejectedValueOnce(new Error('invalid'))
    await findButtonByText(wrapper, '保存')!.trigger('click')
    await flushPromises()
    expect(apiMocks.updateKbDocumentSummary).not.toHaveBeenCalled()

    // 校验通过 → 提交
    await textarea.setValue('新摘要')
    apiMocks.updateKbDocumentSummary.mockResolvedValue({ doc_id: 'd-1', summary: '新摘要' })
    await findButtonByText(wrapper, '保存')!.trigger('click')
    await flushPromises()
    expect(apiMocks.updateKbDocumentSummary).toHaveBeenCalledWith('d-1', { summary: '新摘要' })
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith('摘要已更新')
  })

  it('摘要 B0001 → 表单行内透出，不弹全局 toast', async () => {
    const wrapper = mountDocuments('admin')
    await flushPromises()
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()

    await findButtonByText(wrapper, '编辑摘要')!.trigger('click')
    apiMocks.updateKbDocumentSummary.mockRejectedValue({ code: 'B0001', message: '参数校验失败：摘要超长' })
    await findButtonByText(wrapper, '保存')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.ep-dialog .ep-form-item').attributes('data-error')).toBe(
      '参数校验失败：摘要超长',
    )
    expect(epMocks.ElMessage.error).not.toHaveBeenCalled()
  })

  it('摘要 rules 运行时上限为 200 字（KB_SUMMARY_MAX_LEN，非源码字符串佐证）', () => {
    const rules = KB_SUMMARY_RULES.summary as Array<{ max?: number; message?: string }>
    expect(rules[0]?.max).toBe(KB_SUMMARY_MAX_LEN)
    expect(KB_SUMMARY_MAX_LEN).toBe(200)
    expect(rules[0]?.message).toContain('摘要不能超过')
  })

  it('ai_trainer：上传/删除/重建/摘要编辑置灰 + 悬浮说明，置灰点击不发起请求', async () => {
    const wrapper = mountDocuments('ai_trainer')
    await flushPromises()

    // 上传按钮置灰 + tooltip 说明；点击不打开对话框、不发请求
    const uploadBtn = findButtonByText(wrapper, '上传文档')!
    expect(uploadBtn.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ep-tooltip').attributes('data-content')).toBe(
      '该操作需 admin/supervisor 权限',
    )
    await uploadBtn.trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'KbUploadDialog' }).props('modelValue')).toBe(false)
    expect(apiMocks.uploadKbDocument).not.toHaveBeenCalled()

    // 详情底栏三按钮置灰；点击不进入二次确认、不发请求
    await wrapper.find('.kbd-action').trigger('click')
    await flushPromises()
    for (const label of ['编辑摘要', '重建索引', '删除文档']) {
      const btn = findButtonByText(wrapper, label)!
      expect(btn.attributes('disabled'), `${label} 应置灰`).toBeDefined()
      await btn.trigger('click')
    }
    await flushPromises()
    expect(epMocks.ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(apiMocks.deleteKbDocument).not.toHaveBeenCalled()
    expect(apiMocks.reindexKbDocument).not.toHaveBeenCalled()
    expect(apiMocks.updateKbDocumentSummary).not.toHaveBeenCalled()
  })

  it('admin/supervisor：写操作按钮可用', async () => {
    for (const role of ['admin', 'supervisor']) {
      const wrapper = mountDocuments(role)
      await flushPromises()
      const uploadBtn = findButtonByText(wrapper, '上传文档')!
      expect(uploadBtn.attributes('disabled'), `${role} 上传按钮应可用`).toBeUndefined()
      wrapper.unmount()
    }
  })
})
