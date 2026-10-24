/**
 * 摘要编辑表单规则（f2-rag-console / tasks 4.3）。
 *
 * 抽为模块级常量而非视图内联：
 * - 让单测可在运行时直接断言 200 字上限（不再以 `?raw` 源码字符串佐证规则存在）；
 * - 视图只引用常量，规则口径与 KB_SUMMARY_MAX_LEN 同源（rag_settings.py:47 = 200）。
 */
import type { FormRules } from 'element-plus'
import { KB_SUMMARY_MAX_LEN } from '../types'

/** 摘要表单规则：≤200 字，超长行内标红且不发起请求（spec「摘要超长前端拦截」） */
export const KB_SUMMARY_RULES: FormRules = {
  summary: [
    {
      max: KB_SUMMARY_MAX_LEN,
      message: `摘要不能超过 ${KB_SUMMARY_MAX_LEN} 字`,
      trigger: ['blur', 'change'],
    },
  ],
}
