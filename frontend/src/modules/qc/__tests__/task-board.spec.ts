/**
 * QcTaskBoard 组件单测（f2-qc-console 批次3 / tasks 4.1-4.3）。
 *
 * 覆盖点：
 * - 进入页面调 demo/pending 且双数据源（+records 首页）合并渲染；全走后端，
 *   源码无浏览器本地缓存依赖（读源码佐证，旧演示页缓存行为不回潮）
 * - onMounted 拉一次 + onActivated 再拉一次（keepAlive 激活刷新，首个激活去重）
 * - 行内开检防连点：loading 期间同行按钮 disabled，连点仅一次请求
 * - 已出分行：仅跳详情 /qc/records/:sessionId，不发 replay
 * - 批量勾选上限 QC_BATCH_LIMIT=10：第 11 条被拒并提示「大批量走后端异步（P1）」；
 *   提交后 rejected 分区附 code 展示
 * - 错误白名单：A2003 按钮置灰 + title 悬浮后端原因且不重复请求；
 *   网关 504/B0009 ElMessage 可解释提示；A2002 原始回放表单行内标红
 * - DEV 门控：灌数/原始回放入口存在（vitest define DEV=true）；
 *   源码断言两入口均在 v-if="isDev" 块内（生产构建剔除）
 *
 * 注：jsdom 不支持 :has-text()，按钮用 findAll('button').filter() 按文本定位。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// ── Mocks（vi.mock 工厂提升，mock 函数必须经 vi.hoisted 定义）──
const apiMocks = vi.hoisted(() => ({
  listQcDemoPending: vi.fn(),
  listQcRecords: vi.fn(),
  replayQcSession: vi.fn(),
  checkQcBatch: vi.fn(),
  seedQcDemo: vi.fn(),
  replayQcRaw: vi.fn(),
}))
vi.mock('../api', () => ({ ...apiMocks }))

const epMocks = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('element-plus', () => ({ ...epMocks }))

const routerPush = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: '/qc/tasks', params: {}, query: {} }),
}))

import QcTaskBoard, { QC_BATCH_LIMIT } from '../views/QcTaskBoard.vue'
// ?raw 导入视图源码（vite/client 类型声明），佐证无浏览器本地缓存依赖与 DEV 门控
import VIEW_SOURCE from '../views/QcTaskBoard.vue?raw'

function pendingItem(id: string) {
  return {
    session_id: id,
    scenario: 'A',
    label: `场景-${id}`,
    case_id: `case-${id}`,
    call_id: `call-${id}`,
  }
}

function scoredItem(id: string) {
  return {
    qc_record_id: `rec-${id}`,
    session_id: id,
    case_id: `case-${id}`,
    agent_id: 'agent-1',
    deduction_total: 8,
    compliance_score: 92,
    risk: 'medium' as const,
    grade: null,
    qc_result: 'violation' as const,
    qc_status: 'PENDING_REVIEW' as const,
    create_time: '2026-10-23T00:00:00Z',
  }
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

function mountBoard() {
  return mount(QcTaskBoard)
}

describe('modules/qc/QcTaskBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    epMocks.ElMessage.success = vi.fn()
    epMocks.ElMessage.error = vi.fn()
    epMocks.ElMessage.warning = vi.fn()
    epMocks.ElMessage.info = vi.fn()
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [], total: 0 })
    apiMocks.listQcRecords.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  })

  it('进入页面调 demo/pending + records 首页，双数据源合并渲染', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-pending-1')], total: 1 })
    apiMocks.listQcRecords.mockResolvedValue({
      items: [scoredItem('s-scored-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountBoard()
    await flushPromises()

    expect(apiMocks.listQcDemoPending).toHaveBeenCalledTimes(1)
    expect(apiMocks.listQcRecords).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
    )
    // 待检行与已出分行同表渲染
    const pendingRow = wrapper.find('tr[data-session-id="s-pending-1"]')
    const scoredRow = wrapper.find('tr[data-session-id="s-scored-1"]')
    expect(pendingRow.exists()).toBe(true)
    expect(scoredRow.exists()).toBe(true)
    expect(pendingRow.attributes('data-kind')).toBe('pending')
    expect(scoredRow.attributes('data-kind')).toBe('scored')
    // 待检行有「开始质检」，已出分行只有「查看详情」
    expect(pendingRow.text()).toContain('开始质检')
    expect(scoredRow.text()).toContain('查看详情')
    expect(scoredRow.text()).not.toContain('开始质检')
    // 待检行展示后端返回字段（scenario/label/case_id/call_id）
    expect(pendingRow.text()).toContain('场景-s-pending-1')
    expect(pendingRow.text()).toContain('case-s-pending-1')
    expect(pendingRow.text()).toContain('call-s-pending-1')
  })

  it('全走后端：视图源码无浏览器本地缓存依赖', () => {
    // 拼接构造检索词，避免本测试自身被 grep 误命中
    const forbidden = ['local', 'Storage'].join('')
    expect(VIEW_SOURCE).not.toContain(forbidden)
  })

  it('onMounted 拉一次 + onActivated 再拉一次（keepAlive 激活刷新）', async () => {
    const wrapper = mountBoard()
    await vi.waitFor(() => expect(apiMocks.listQcDemoPending).toHaveBeenCalledTimes(1))

    // 模拟 keepAlive 缓存激活：onActivated 绑定的就是 setup 里的 fetchBoard
    const vm = wrapper.vm as unknown as Record<string, unknown>
    const setupState = (vm as { $?: { setupState?: Record<string, unknown> } }).$?.setupState
    const fetchBoard = setupState?.fetchBoard as (() => Promise<void>) | undefined
    expect(fetchBoard).toBeDefined()
    await fetchBoard!()
    await vi.waitFor(() => expect(apiMocks.listQcDemoPending).toHaveBeenCalledTimes(2))
  })

  it('行内开检防连点：loading 期间同行按钮 disabled，连点仅一次请求', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.replayQcSession.mockReturnValue(new Promise(() => {})) // 挂起模拟在途
    const wrapper = mountBoard()
    await flushPromises()

    const btn = wrapper.find('tr[data-session-id="s-1"] button')
    expect(btn.text()).toContain('开始质检')
    await btn.trigger('click')
    await btn.trigger('click')
    await btn.trigger('click')

    expect(apiMocks.replayQcSession).toHaveBeenCalledTimes(1)
    expect(apiMocks.replayQcSession.mock.calls[0][0]).toBe('s-1')
    // 在途期间同行按钮 disabled（防连点、禁重复触发）
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('已出分行点击仅跳详情，不发 replay（不自动重跑）', async () => {
    apiMocks.listQcRecords.mockResolvedValue({
      items: [scoredItem('s-scored-9')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountBoard()
    await flushPromises()

    const row = wrapper.find('tr[data-session-id="s-scored-9"]')
    await row.find('button').trigger('click')

    expect(routerPush).toHaveBeenCalledWith('/qc/records/s-scored-9')
    expect(apiMocks.replayQcSession).not.toHaveBeenCalled()
  })

  it(`批量勾选第 ${QC_BATCH_LIMIT + 1} 条被拒并提示「大批量走后端异步（P1）」`, async () => {
    const items = Array.from({ length: QC_BATCH_LIMIT + 1 }, (_, i) => pendingItem(`s-${i}`))
    apiMocks.listQcDemoPending.mockResolvedValue({ items, total: items.length })
    const wrapper = mountBoard()
    await flushPromises()

    // 行内复选框限定 tbody（表头全选框也在 thead 用 qtb-check，不计入行勾选数）
    const boxes = wrapper.findAll('tbody input.qtb-check')
    expect(boxes.length).toBe(QC_BATCH_LIMIT + 1)
    for (let i = 0; i < QC_BATCH_LIMIT; i++) {
      await boxes[i].setValue(true)
    }
    expect(wrapper.text()).toContain(`已选 ${QC_BATCH_LIMIT}/${QC_BATCH_LIMIT}`)

    // 第 11 条：勾选被拒 + 提示
    await boxes[QC_BATCH_LIMIT].setValue(true)
    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining('大批量走后端异步（P1）'),
    )
    expect(wrapper.text()).toContain(`已选 ${QC_BATCH_LIMIT}/${QC_BATCH_LIMIT}`)
    // 被拒后该复选框回退为未选中
    expect((boxes[QC_BATCH_LIMIT].element as HTMLInputElement).checked).toBe(false)
  })

  it('表头全选：勾选当前页全部待检行（已出分行不参与），取消全选清空', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({
      items: [pendingItem('s-p1'), pendingItem('s-p2')],
      total: 2,
    })
    apiMocks.listQcRecords.mockResolvedValue({
      items: [scoredItem('s-scored-1')],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const wrapper = mountBoard()
    await flushPromises()

    const headerBox = wrapper.find('thead input.qtb-check')
    expect(headerBox.exists()).toBe(true)

    // 全选：2 条待检全勾，已出分行无复选框不参与
    await headerBox.setValue(true)
    expect(wrapper.text()).toContain('已选 2/10')
    const rowBoxes = wrapper.findAll('tbody input.qtb-check')
    expect(rowBoxes.length).toBe(2) // 仅待检行有复选框
    expect((rowBoxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((rowBoxes[1].element as HTMLInputElement).checked).toBe(true)
    // 全选后表头为选中态、非半选态
    expect((headerBox.element as HTMLInputElement).checked).toBe(true)
    expect((headerBox.element as HTMLInputElement).indeterminate).toBe(false)

    // 取消全选：清空
    await headerBox.setValue(false)
    expect(wrapper.text()).toContain('已选 0/10')
    expect((rowBoxes[0].element as HTMLInputElement).checked).toBe(false)
  })

  it(`表头全选超上限：待检 ${QC_BATCH_LIMIT + 2} 条时只选前 ${QC_BATCH_LIMIT} 条并提示`, async () => {
    const items = Array.from({ length: QC_BATCH_LIMIT + 2 }, (_, i) => pendingItem(`s-${i}`))
    apiMocks.listQcDemoPending.mockResolvedValue({ items, total: items.length })
    const wrapper = mountBoard()
    await flushPromises()

    await wrapper.find('thead input.qtb-check').setValue(true)

    expect(wrapper.text()).toContain(`已选 ${QC_BATCH_LIMIT}/${QC_BATCH_LIMIT}`)
    expect(epMocks.ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining('大批量走后端异步（P1）'),
    )
    // 前 10 条勾选，第 11/12 条未勾
    const rowBoxes = wrapper.findAll('tbody input.qtb-check')
    expect((rowBoxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((rowBoxes[QC_BATCH_LIMIT - 1].element as HTMLInputElement).checked).toBe(true)
    expect((rowBoxes[QC_BATCH_LIMIT].element as HTMLInputElement).checked).toBe(false)
    expect((rowBoxes[QC_BATCH_LIMIT + 1].element as HTMLInputElement).checked).toBe(false)
    // 截断后未全选：表头呈半选态
    expect((wrapper.find('thead input.qtb-check').element as HTMLInputElement).indeterminate).toBe(true)
  })

  it('表头半选态：仅勾部分行时 indeterminate=true，勾满后变回 false', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({
      items: [pendingItem('s-a'), pendingItem('s-b')],
      total: 2,
    })
    const wrapper = mountBoard()
    await flushPromises()

    const headerEl = wrapper.find('thead input.qtb-check').element as HTMLInputElement
    expect(headerEl.indeterminate).toBe(false) // 初始无勾选

    // 勾 1/2 → 半选
    await wrapper.findAll('tbody input.qtb-check')[0].setValue(true)
    expect(headerEl.indeterminate).toBe(true)
    expect(headerEl.checked).toBe(false)

    // 勾 2/2 → 全选
    await wrapper.findAll('tbody input.qtb-check')[1].setValue(true)
    expect(headerEl.indeterminate).toBe(false)
    expect(headerEl.checked).toBe(true)
  })

  it('批量提交：check/batch 携带勾选集合，rejected 分区附 code 展示', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({
      items: [pendingItem('s-a'), pendingItem('s-b')],
      total: 2,
    })
    apiMocks.checkQcBatch.mockResolvedValue({
      batch_id: 'b-1',
      accepted: [
        {
          qc_record_id: 'rec-a',
          session_id: 's-a',
          deduction_total: 0,
          compliance_score: 100,
          risk: 'low',
          grade: null,
          qc_result: 'compliant',
          qc_status: 'DONE',
        },
      ],
      rejected: [{ session_id: 's-b', code: 'A2003' }],
    })
    const wrapper = mountBoard()
    await flushPromises()

    const boxes = wrapper.findAll('tbody input.qtb-check')
    await boxes[0].setValue(true)
    await boxes[1].setValue(true)

    const batchBtn = findButtonByText(wrapper, '批量质检')
    expect(batchBtn).toBeDefined()
    await batchBtn!.trigger('click')
    await flushPromises()

    expect(apiMocks.checkQcBatch).toHaveBeenCalledTimes(1)
    const body = apiMocks.checkQcBatch.mock.calls[0][0] as {
      batch_id: string
      session_ids: string[]
    }
    expect(body.session_ids.sort()).toEqual(['s-a', 's-b'])
    expect(body.batch_id).toBeTruthy()
    // 结果分区：accepted / rejected（rejected 附 code）
    expect(wrapper.text()).toContain('已受理（1）')
    expect(wrapper.text()).toContain('已拒收（1）')
    expect(wrapper.text()).toContain('code=A2003')
  })

  it('A2003：该行质检按钮置灰 + title 悬浮后端原因，不重复发起请求', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.replayQcSession.mockRejectedValue({
      message: '对话不可质检（通话未结束、全文未齐或通话主数据不存在）（trace: abc12345）',
      code: 'A2003',
      trace: 'tr_abc12345',
    })
    const wrapper = mountBoard()
    await flushPromises()

    const btn = wrapper.find('tr[data-session-id="s-1"] button')
    await btn.trigger('click')
    await flushPromises()

    expect(apiMocks.replayQcSession).toHaveBeenCalledTimes(1)
    // 置灰 + 悬浮说明后端 message
    expect(btn.attributes('disabled')).toBeDefined()
    expect(btn.attributes('title')).toContain('对话不可质检')
    // 再次点击不重复发起请求
    await btn.trigger('click')
    await flushPromises()
    expect(apiMocks.replayQcSession).toHaveBeenCalledTimes(1)
  })

  it('网关 504/B0009：ElMessage 可解释提示（含白名单文案与 trace 后 8 位）', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.replayQcSession.mockRejectedValue({
      message: '服务暂不可用（trace: 89abcdef）',
      code: 'B0009',
      trace: 'tr_0123456789abcdef',
    })
    const wrapper = mountBoard()
    await flushPromises()

    await wrapper.find('tr[data-session-id="s-1"] button').trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('服务暂不可用'))
    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('89abcdef'))
    // B0009 非 A2003：按钮不置灰，可重试
    const btn = wrapper.find('tr[data-session-id="s-1"] button')
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('未列入白名单的 A20xx：默认分支 message 直出', async () => {
    apiMocks.listQcDemoPending.mockResolvedValue({ items: [pendingItem('s-1')], total: 1 })
    apiMocks.replayQcSession.mockRejectedValue({
      message: '会话正在质检中（trace: abcdef01）',
      code: 'A2006',
      trace: 'tr_abcdef01',
    })
    const wrapper = mountBoard()
    await flushPromises()

    await wrapper.find('tr[data-session-id="s-1"] button').trigger('click')
    await flushPromises()

    expect(epMocks.ElMessage.error).toHaveBeenCalledWith(
      expect.stringContaining('会话正在质检中'),
    )
  })

  it('DEV 门控：灌数与原始回放入口在 DEV 下渲染', async () => {
    const wrapper = mountBoard()
    await flushPromises()
    // vitest.config define import.meta.env.DEV=true
    expect(findButtonByText(wrapper, '灌入演示数据')).toBeDefined()
    expect(findButtonByText(wrapper, '原始回放')).toBeDefined()
  })

  it('DEV 门控源码佐证：两入口均在 v-if="isDev" 块内（生产构建剔除）', () => {
    // 灌数与原始回放按钮必须位于同一个 v-if="isDev" template 块内，
    // 生产构建（DEV=false）不渲染入口与触发器
    const devBlock = VIEW_SOURCE.match(/<template v-if="isDev">([\s\S]*?)<\/template>/)
    expect(devBlock).not.toBeNull()
    expect(devBlock![1]).toContain('灌入演示数据')
    expect(devBlock![1]).toContain('原始回放')
  })

  it('灌数成功提示并刷新队列', async () => {
    apiMocks.seedQcDemo.mockResolvedValue({
      session_ids: ['s-new-1', 's-new-2'],
      sessions: [pendingItem('s-new-1'), pendingItem('s-new-2')],
      token: 't',
      reviewer_id: 'r',
    })
    const wrapper = mountBoard()
    await flushPromises()
    apiMocks.listQcDemoPending.mockClear()

    await findButtonByText(wrapper, '灌入演示数据')!.trigger('click')
    await flushPromises()

    expect(apiMocks.seedQcDemo).toHaveBeenCalledTimes(1)
    expect(epMocks.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('2 条'))
    expect(apiMocks.listQcDemoPending).toHaveBeenCalledTimes(1) // 成功后刷新队列
  })

  it('A2002：原始回放表单行内标红（不叠 B0001）', async () => {
    apiMocks.replayQcRaw.mockRejectedValue({
      message: '质检入参不合法（trace: abcdef02）',
      code: 'A2002',
      trace: 'tr_abcdef02',
    })
    const wrapper = mountBoard()
    await flushPromises()

    await findButtonByText(wrapper, '原始回放')!.trigger('click')
    const textarea = wrapper.find('textarea.qtb-textarea')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue(
      JSON.stringify({
        session_id: 's-raw-1',
        case_id: 'c-1',
        call_ended: true,
        turns: [{ turn_id: 't1', speaker: 'agent', text: '您好', turn_index: 0 }],
      }),
    )
    await findButtonByText(wrapper, '提交回放')!.trigger('click')
    await flushPromises()

    // 行内标红：错误文案渲染 + textarea 标红 class；不走 ElMessage 弹窗
    const fieldError = wrapper.find('.qtb-field-error')
    expect(fieldError.exists()).toBe(true)
    expect(fieldError.text()).toContain('质检入参不合法')
    expect(textarea.classes()).toContain('qtb-field--error')
    expect(epMocks.ElMessage.error).not.toHaveBeenCalled()
  })

  it('原始回放：非法 JSON 行内拦截，不发请求', async () => {
    const wrapper = mountBoard()
    await flushPromises()

    await findButtonByText(wrapper, '原始回放')!.trigger('click')
    await wrapper.find('textarea.qtb-textarea').setValue('{not-json')
    await findButtonByText(wrapper, '提交回放')!.trigger('click')
    await flushPromises()

    expect(apiMocks.replayQcRaw).not.toHaveBeenCalled()
    expect(wrapper.find('.qtb-field-error').text()).toContain('JSON 解析失败')
  })
})
