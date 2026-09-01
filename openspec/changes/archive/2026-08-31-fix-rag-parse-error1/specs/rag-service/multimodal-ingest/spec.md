## ADDED Requirements

### Requirement: ASR Key 可用性判定与错误定位

系统 SHALL 在音频转入 ASR 转写前判定当前所选 ASR Provider 所需的凭据是否真实可用，避免"已配置可用 Key 却仍判空"的误报：

- **可用性判定**：当所选 Provider 为 DashScope 时，MUST 读取 `DASHSCOPE_API_KEY`（含等价的阿里千问 DashScope Key）；当该 Key 非空且有效时，MUST 正常调用 DashScope ASR 完成转写，**不得抛「DASHSCOPE_API_KEY not configured」**；仅当所选 Provider 所需 Key 确实为空时才判定"未配置"。
- **错误定位**：当确无可用 Key 而抛 A3001 时，异常/错误信息 MUST 明确指明缺失的是哪个配置项（如 `DASHSCOPE_API_KEY`），便于定位；不得模糊为笼统的"文档解析失败"。
- **降级兼容**：Key 缺失时的行为 MUST 与既有「OCR/ASR 解析失败降级」契约一致——`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走占位入库（status=failed + degraded=true，不抛 500），默认关闭时返回 A3001 + status=failed。

本 Requirement 为既有「音频 ASR 转写支持」的兼容性修正，不改变既有 VAD 分片、≤60s 分片、开场白裁剪、Whisper 可切换、熔断 `rag:asr:degraded` 等既有行为。

#### Scenario: 已配置 DashScope Key 时 mp3 转写成功（不再 A3001）

- **GIVEN** 环境中已配置有效的 `DASHSCOPE_API_KEY`（或等价千问 DashScope Key），`ASR_PROVIDER=dashscope`
- **WHEN** 管理员上传一段 90s 催收通话 `.mp3`，category=case
- **THEN** 系统调用 DashScope ASR 转写成功，经清洗、分块写入双索引，返回 doc_id + status=active，source=asr，不再返回 A3001「DASHSCOPE_API_KEY not configured」

#### Scenario: 未配置任何可用 Key 时返回可定位的 A3001

- **GIVEN** `DASHSCOPE_API_KEY` 为空，`ASR_PROVIDER=dashscope`，降级开关关闭（默认）
- **WHEN** 管理员上传 `.mp3` 文件
- **THEN** 系统返回 A3001，status=failed，且错误信息明确提示缺失 `DASHSCOPE_API_KEY`

#### Scenario: 降级开关开启时 Key 缺失走占位入库

- **GIVEN** `DASHSCOPE_API_KEY` 为空，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **WHEN** 管理员上传 `.mp3` 文件
- **THEN** 系统不抛 500，文档 status=failed、不写索引、落解析失败日志，响应标记 degraded=true

#### Scenario: 切换 Whisper 且模型可用时转写正常

- **GIVEN** `ASR_PROVIDER=whisper`，本地模型加载成功
- **WHEN** 管理员上传 `.mp3` / `.wav` 文件
- **THEN** 系统调用 Whisper Provider 完成转写，对外请求/响应契约不变，处理过程与既有行为一致
