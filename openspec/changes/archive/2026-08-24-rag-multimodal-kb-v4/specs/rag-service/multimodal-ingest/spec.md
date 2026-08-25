# RAG Multimodal Ingest Specification

## MODIFIED Requirements

### Requirement: 音频 ASR 转写支持

系统 SHALL 支持 .mp3 / .wav / .m4a 格式音频上传，通过 ASR Provider 抽象（DashScope Paraformer 默认，Whisper 可切换）执行语音转文字；**MUST 在转写前执行 VAD 分片**：对整段音频执行静音裁剪（去除首尾静音段），并按 ≤ 60s 时长切分为多个分片，逐片转写后按时间顺序拼接结果；**MUST 在转写文本拼接完成后执行"通话开场白"规则裁剪（审计 D8 落地）**：对转写文本开头进行催收通话开场模式规则识别（如"喂 / 你好 / 您好 / 请问是 / 先生 / 女士 / 我们是 XX 银行（公司）/ 您的信用卡逾期"等问候与身份确认关键词），命中则裁剪至首个有效业务内容起点；规则裁剪为纯规则实现（正则 + 关键词表，配置于 `conf/asr_opening_rules.yaml`），**不引入 LLM 依赖、不引入新第三方依赖**；语义级 VAD（说话人/意图级开场识别）仍为后续迭代 Backlog，不影响本次规则裁剪；提取失败 A3001，超时 60s、3 次重试、熔断 `rag:asr:degraded`。ASR Provider 抽象 MUST 支持至少两个可切换实现：DashScope Paraformer（默认）与 Whisper（本地），Whisper 实现 MUST 可完成 WAV/MP3 转写并返回与 DashScope 一致的转写文本契约，切换 `ASR_PROVIDER` 配置不影响对外请求/响应契约。

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

#### Scenario: 通话开场白规则裁剪

- **WHEN** 管理员上传一段以"喂，你好，请问是李女士吗？我们是XX公司法务部"开头的催收通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=true`
- **THEN** 入库转写文本中该开场白语句被裁剪，首条有效业务内容（如"您有一笔逾期账单需要处理"）成为文本起点，其余转写内容不变

#### Scenario: 开关关闭时开场白保留原样

- **WHEN** 管理员上传含开场白的通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=false`（默认）
- **THEN** 转写文本与 V3 行为完全一致，开场白不被裁剪，不影响既有分块与入库

#### Scenario: 无开场白音频不受影响

- **WHEN** 管理员上传一段直接从业务内容开始的 20s 录音（如"您有一笔逾期账单"），且开场白裁剪开关开启
- **THEN** 规则识别不命中，转写文本原样入库，不误删任何有效业务内容
