# Baseline 降级说明（qc-service）

## 降级原因

T0.3 要求优先录制迁移前的真实 LLM 输出；本机 `.env` 中 `DASHSCOPE_API_KEY` 与 `DEEPSEEK_API_KEY` 均为空，无可用 LLM 配额，无法发起真实调用。

## 实际录制内容

- 文件：`qc_review_prompt.txt`
- 内容：迁移前由 `build_system_prompt(load_qc_review_prompt())` 生成的 system prompt，以及一段示例 turns 组装出的 user 消息（JSON）
- 说明：qc 现有 prompt 无 Jinja 占位符，渲染结果即上述文本

## 迁移后比对方式

W2 迁移后重新生成同一份 prompt 文本做人工比对；不比对模型输出（无配额）。除 `goal→task`、`constraints→input_constraint`、`schema→output_schema` 的字段改名外，正文措辞应逐字一致。
