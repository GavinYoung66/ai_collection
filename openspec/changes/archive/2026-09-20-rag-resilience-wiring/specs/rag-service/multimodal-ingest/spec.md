## ADDED Requirements

### Requirement: OCR/ASR HTTP 熔断规范化且不阻塞入库

图片 OCR 与音频 ASR 是直接 HTTP 调用，MUST 外包共享熔断器，MUST NOT 改走 MCP `call_tool`。熔断 key MUST 为 `rag:mcp_degraded:ocr` 与 `rag:mcp_degraded:asr`；`fail_max=3`，`reset_timeout=60s`。系统 MUST NOT 再写入或查询 `rag:ocr:degraded` / `rag:asr:degraded`。

熔断 open 时：

- OCR MUST 短路外部调用并视为解析失败：标记 OCR 失败，MUST NOT HTTP 500，MUST NOT 卡死 worker；不得把熔断当成空白图识别成功（A3002）。开关关闭时仍走 A3001 + status=failed；`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有占位入库。内嵌图片单张失败仍 MUST skip + warning，不阻断正文入库。
- ASR MUST 短路外部调用并返回转写失败标记：标记 ASR 失败，MUST NOT HTTP 500，MUST NOT 卡死 worker；开关与占位入库契约与 OCR 相同。

#### Scenario: OCR 熔断不 500 且不卡入库

- **WHEN** `rag:mcp_degraded:ocr` 为 open，管理员上传 PNG，降级开关关闭
- **THEN** 系统不调 OCR HTTP，文档标记 OCR 失败（A3001 + status=failed），HTTP 不是 500，worker 不卡死；`rag:ocr:degraded` MUST NOT 被 SET

#### Scenario: ASR 熔断不 500 且不卡入库

- **WHEN** `rag:mcp_degraded:asr` 为 open，管理员上传 MP3，降级开关关闭
- **THEN** 系统不调 ASR HTTP，文档标记转写失败（A3001 + status=failed），HTTP 不是 500，worker 不卡死；`rag:asr:degraded` MUST NOT 被 SET

#### Scenario: OCR 熔断且降级开关开启走占位

- **WHEN** `rag:mcp_degraded:ocr` 为 open 且 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 系统不返回 500，文档按占位入库处理（status=failed，不写索引，degraded=true）

## MODIFIED Requirements

### Requirement: 图片 OCR 解析支持

系统 SHALL 支持 .png / .jpg 格式图片上传，通过 PaddleOCR 独立服务（endpoint `/ocr`，`lang="ch"` 中英文混合）提取图片文字；识别失败时按降级策略处理（见下方"OCR/ASR 解析失败降级"Requirement）：默认（开关关闭）走统一 A3001 文档解析失败分支，与 V2 行为一致。OCR HTTP 调用 MUST 受共享熔断器保护（key `rag:mcp_degraded:ocr`，`fail_max=3`，`reset_timeout=60s`），超时沿用 30s；MUST NOT 再手写 `rag:ocr:degraded`，MUST NOT 把该调用改成 MCP `call_tool`。熔断 open 时 MUST 短路调用并按解析失败处理（不 500、不卡死）。

#### Scenario: 制度截图 OCR 成功入库

- **WHEN** 管理员上传一张包含"逾期协商流程"的 JPG 制度截图，category=regulation
- **THEN** OCR 服务返回文字提取结果，经清洗分块写入双索引，文档 status=active，后续以该图文字检索可命中对应 chunk

#### Scenario: OCR 服务熔断降级

- **WHEN** PaddleOCR 连续 3 次失败后，管理员再上传 PNG 文件，且降级开关关闭（默认）
- **THEN** 系统直接短路调用，返回 A3001，status=failed，并在 60s 内所有图片上传均走熔断短路径

#### Scenario: OCR 服务熔断且降级开关开启

- **WHEN** PaddleOCR 连续 3 次失败后，管理员再上传 PNG 文件，且 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 系统不返回 A3001，文档按"占位入库"降级策略处理（见下方降级 Requirement），status=failed

#### Scenario: 纯空白图无文字

- **WHEN** 管理员上传一张纯白无文字 PNG 文件
- **THEN** OCR 返回空文本，分块服务产生 0 有效 chunk，走 A3002 文本切片失败，status=failed

### Requirement: 音频 ASR 转写支持

系统 SHALL 支持 .mp3 / .wav / .m4a / .amr / .wma 格式音频上传，通过 ASR Provider 抽象（DashScope Paraformer 默认，Whisper 可切换）执行语音转文字；**MUST 在转写前执行 VAD 分片**：对整段音频执行静音裁剪（去除首尾静音段），并按 ≤ 60s 时长切分为多个分片，逐片转写后按时间顺序拼接结果；**MUST 在转写文本拼接完成后执行"通话开场白"规则裁剪（审计 D8 落地）**：对转写文本开头进行催收通话开场模式规则识别（如"喂 / 你好 / 您好 / 请问是 / 先生 / 女士 / 我们是 XX 银行（公司）/ 您的信用卡逾期"等问候与身份确认关键词），命中则裁剪至首个有效业务内容起点；规则裁剪为纯规则实现（正则 + 关键词表，配置于 `conf/asr_opening_rules.yaml`），**不引入 LLM 依赖、不引入新第三方依赖**；语义级 VAD（说话人/意图级开场识别）仍为后续迭代 Backlog，不影响本次规则裁剪；提取失败 A3001，超时 60s。DashScope ASR HTTP MUST 受共享熔断器保护（key `rag:mcp_degraded:asr`，`fail_max=3`，`reset_timeout=60s`）；MUST NOT 再手写 3 次指数退避熔断与 `rag:asr:degraded`，MUST NOT 把该调用改成 MCP `call_tool`。熔断 open 时 MUST 返回转写失败标记且不 500、不卡死。ASR Provider 抽象 MUST 支持至少两个可切换实现：DashScope Paraformer（默认）与 Whisper（本地），Whisper 实现 MUST 可完成 WAV/MP3 转写并返回与 DashScope 一致的转写文本契约，切换 `ASR_PROVIDER` 配置不影响对外请求/响应契约。

**V5 扩展（AMR/WMA）**：系统 SHALL 支持 .amr / .wma 电话录音格式。解码策略：先经 **ffmpeg**（服务端二进制依赖，pydub 封装）解码为标准 WAV，再复用既有 VAD 静音裁剪 + ≤60s 分片 + ASR Provider 转写管线（**Client 侧解码，Provider 侧保持接收 WAV/音频流契约不变**，即 AMR/WMA 接入不改变 ASR Provider 抽象对外契约）；`KB_ASR_AMR_WMA_ENABLED`（默认 false）门控。损坏/无法解码的 AMR/WMA MUST 返回业务错误（A3001，或 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有降级占位分支），**不得卡死/崩溃 worker、不得影响后续请求**。M4A 保持既有支持不变。

#### Scenario: 通话录音转写入库成功

- **WHEN** 管理员上传一段 90s 催收通话 MP3 录音，category=case
- **THEN** 系统按 60s 分片 → 逐片去静音转写 → 拼接转写文本（开场白裁剪开关开启时对文本开头进行规则裁剪）→ 分块写入双索引，文档 status=active，kb_documents.source 字段记录为 "asr"

#### Scenario: 90s 录音 VAD 分片后逐片转写

- **WHEN** 管理员上传 90s 通话 WAV 文件
- **THEN** 系统将音频裁剪静音后切分为至少 2 个 ≤60s 分片，逐片调用 Provider 转写，最终入库文本为各分片转写结果按时间顺序拼接的完整内容，且不丢失任一分片文本

#### Scenario: 短静音音频单分片转写

- **WHEN** 管理员上传 20s 无静音 WAV 文件
- **THEN** VAD 检测无静音段、总时长 ≤60s，系统以单个分片直接转写，不强制切分，结果与整段转写一致

#### Scenario: ASR Provider 切换为 Whisper 后转写正常

- **WHEN** 管理员在配置中切换 ASR_PROVIDER=whisper 后上传 WAV 文件，Whisper 模型可用
- **THEN** 请求/响应契约无变化，系统内部调用 Whisper Provider 完成转写，转写结果仍按 V1/V2 既有清洗规则去空行与脏数据，开场白规则裁剪对转写文本同样生效

#### Scenario: Whisper Provider 无 Key 或模型不可用时降级

- **WHEN** ASR_PROVIDER=whisper 但本地模型加载失败或转写异常
- **THEN** 系统按既有 ASR 异常路径返回 A3001（或降级开关开启时的占位入库），不抛未捕获异常，不影响服务其他接口

#### Scenario: 损坏音频解析失败

- **WHEN** 管理员上传损坏加密 M4A 文件
- **THEN** ASR 调用抛出异常，系统返回 A3001，status=failed，不写入任何索引

#### Scenario: AMR 录音 ffmpeg 解码后正常转写入库

- **WHEN** 管理员上传一段 .amr 电话录音且 `KB_ASR_AMR_WMA_ENABLED=true`
- **THEN** 系统经 ffmpeg 解码为 WAV 后走既有 VAD 分片 + ASR Provider 转写管线，转写文本分块写入双索引，status=active，source=asr

#### Scenario: WMA 录音正常转写入库

- **WHEN** 管理员上传一段 .wma 电话录音且 `KB_ASR_AMR_WMA_ENABLED=true`
- **THEN** 系统经 ffmpeg 解码后走同一转写管线，正常入库并可被检索命中

#### Scenario: 损坏 AMR 返回业务错误且 worker 不卡死

- **WHEN** 管理员上传一个损坏/无法解码的 .amr 文件
- **THEN** 系统返回 A3001（或降级开关开启时走占位入库分支），处理该文件的 worker 不卡死、不崩溃，后续上传请求可正常处理

#### Scenario: 开关关闭时 AMR/WMA 按既有白名单外处理

- **WHEN** `KB_ASR_AMR_WMA_ENABLED=false`（默认），管理员上传 .amr 文件
- **THEN** 系统按格式白名单外处理返回 A3001，行为与 V4 完全一致

#### Scenario: 通话开场白规则裁剪

- **WHEN** 管理员上传一段以"喂，你好，请问是李女士吗？我们是XX公司法务部"开头的催收通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=true`
- **THEN** 入库转写文本中该开场白语句被裁剪，首条有效业务内容（如"您有一笔逾期账单需要处理"）成为文本起点，其余转写内容不变

#### Scenario: 开关关闭时开场白保留原样

- **WHEN** 管理员上传含开场白的通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=false`（默认）
- **THEN** 转写文本与 V3 行为完全一致，开场白不被裁剪，不影响既有分块与入库

#### Scenario: 无开场白音频不受影响

- **WHEN** 管理员上传一段直接从业务内容开始的 20s 录音（如"您有一笔逾期账单"），且开场白裁剪开关开启
- **THEN** 规则识别不命中，转写文本原样入库，不误删任何有效业务内容

### Requirement: OCR/ASR 解析失败降级

当 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`（默认 false=V2 原行为）时，系统 SHALL 在 OCR/ASR 解析阶段失败（服务未部署、Key 未配置、熔断、超时、解析异常）时，不再直接返回 A3001 失败，而是执行以下降级策略：

1. **占位入库**：将文档标记 status=failed 并落一条解析失败日志（含原因），不写任何索引，返回 A3001 的语义保留在日志中；
2. 降级响应 MUST 在返回/日志中标记 `degraded=true`，供监控与排查；
3. 熔断标识（`rag:mcp_degraded:ocr` / `rag:mcp_degraded:asr`）在降级开启时仍正常置位与 TTL/reset_timeout 过期。系统 MUST NOT 再置位 `rag:ocr:degraded` / `rag:asr:degraded`。

> **规格收敛（审计 D3）**：早期 spec 曾描述"仅文本入库"策略（请求附带纯文本时跳过 OCR/ASR 直接入库，status=active）；当前上传接口（`POST /kb/documents`，multipart file+category）无纯文本附带参数，该分支不可触发，V3 不实现。"仅文本入库"收敛为占位入库（唯一实现策略），如后续接口扩展附带文本能力，再迭代实现。

当开关关闭（默认值），行为与 V2 完全一致：OCR/ASR 解析失败直接 A3001 + status=failed。

#### Scenario: OCR 服务未部署时占位失败

- **WHEN** ocr-service 未部署，管理员上传 JPG 制度截图，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 系统不调用 OCR，文档 status=failed，不写索引，落解析失败日志，响应标记 degraded=true（"仅文本入库"未实现，见降级 Requirement 规格收敛说明）

#### Scenario: ASR 无 Key 且无附带文本时占位失败

- **WHEN** DashScope Key 未配置、无附带文本，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 文档 status=failed，不写索引，落解析失败日志，接口返回 code="0" 且 data 标记 degraded=true（不抛 500）

#### Scenario: 降级开关关闭保持 V2 行为

- **WHEN** `KB_OCR_ASR_DEGRADED_KEEP_TEXT=false`（默认），OCR 服务不可用
- **THEN** 系统按 V2 原行为返回 A3001、status=failed，不产生任何降级入库

### Requirement: ASR Key 可用性判定与错误定位

系统 SHALL 在音频转入 ASR 转写前判定当前所选 ASR Provider 所需的凭据是否真实可用，避免"已配置可用 Key 却仍判空"的误报：

- **可用性判定**：当所选 Provider 为 DashScope 时，MUST 读取 `DASHSCOPE_API_KEY`（含等价的阿里千问 DashScope Key）；当该 Key 非空且有效时，MUST 正常调用 DashScope ASR 完成转写，**不得抛「DASHSCOPE_API_KEY not configured」**；仅当所选 Provider 所需 Key 确实为空时才判定"未配置"。
- **错误定位**：当确无可用 Key 而抛 A3001 时，异常/错误信息 MUST 明确指明缺失的是哪个配置项（如 `DASHSCOPE_API_KEY`），便于定位；不得模糊为笼统的"文档解析失败"。
- **降级兼容**：Key 缺失时的行为 MUST 与既有「OCR/ASR 解析失败降级」契约一致——`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走占位入库（status=failed + degraded=true，不抛 500），默认关闭时返回 A3001 + status=failed。

本 Requirement 为既有「音频 ASR 转写支持」的兼容性修正，不改变既有 VAD 分片、≤60s 分片、开场白裁剪、Whisper 可切换等既有行为；熔断 key MUST 为 `rag:mcp_degraded:asr`。

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
