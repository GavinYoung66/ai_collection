# Baseline 降级说明（rag-service）

## 降级原因

T0.3 要求优先录制迁移前的真实 LLM 输出；本机 `.env` 中 `DASHSCOPE_API_KEY` 与 `DEEPSEEK_API_KEY` 均为空，无可用 LLM 配额，无法发起真实调用。

## 实际录制内容

- 文件：`rag_answer_prompt.txt`
- 内容：迁移前由 `build_answer_prompt(context, query)` 渲染出的 system 与 user 文本（示例 query/context 见文件内标注）

## 迁移后比对方式

W3 迁移后使用相同 query/context 重新渲染，人工比对 prompt 文本；不比对模型输出（无配额）。`{query}`/`{context}` 变量语义与正文措辞应保持一致。
