# RAG Document Ingest — Delta Spec（rag-service-final-fix）

## ADDED Requirements

### Requirement: 上传大小上限

系统 MUST 限制单次上传文件大小：超过 `KB_MAX_FILE_SIZE`（默认 20MB，对齐 DD-03 §5.1/§8）的上传请求返回 B0001（参数错误）。大小校验 MUST 在将文件内容读入内存之前完成，避免超大文件先占内存再被拒绝。

#### Scenario: 超过上限返回 B0001

- **WHEN** 管理员上传一个 25MB 的文件
- **THEN** 系统返回错误码 B0001，不产生任何入库写入，服务内存占用不受文件实际大小影响

#### Scenario: 边界值放行

- **WHEN** 管理员上传一个恰好 20MB 的文件
- **THEN** 系统按既有流程正常解析入库，返回 doc_id + status=active

### Requirement: 解析超时控制

系统 MUST 对文档解析（文本提取）设置超时上限 `KB_PARSE_TIMEOUT`（默认 30s，对齐 DD-03 §6.1/§8）：解析超时按既有解析失败路径处理（返回 A3001、文档状态置 failed；`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有降级分支），不得让请求无限挂起。

#### Scenario: 解析超时按失败处理

- **WHEN** 文档解析耗时超过 KB_PARSE_TIMEOUT 仍未完成
- **THEN** 系统中止解析，返回 A3001，文档状态为 failed，不产生部分索引残留

#### Scenario: 正常解析不受影响

- **WHEN** 文档解析在超时阈值内完成
- **THEN** 系统按既有流程继续分块、向量化并入库

### Requirement: 健康检查依赖探活

系统 MUST 提供 `GET /health` 端点并真实探测 PostgreSQL、Milvus、Redis、Embedding 四项依赖的可用性（对齐 DD-03 §2）；任一依赖不可用时健康状态为非 healthy（响应字段契约以全局 step1/01 /health 规范为准）。健康检查探活异常 MUST NOT 导致接口返回 500。重复的 /health 端点 MUST 统一为单一实现，避免双端点行为漂移。

#### Scenario: 全部依赖正常

- **WHEN** PostgreSQL、Milvus、Redis、Embedding 均可用
- **THEN** /health 返回 healthy 状态

#### Scenario: 单依赖不可用

- **WHEN** Milvus 不可用
- **THEN** /health 返回非 healthy 状态，HTTP 状态码与响应结构遵循全局 step1/01 /health 规范

#### Scenario: 探活超时不抛 500

- **WHEN** 任一依赖探活调用超时
- **THEN** /health 判定该依赖不可用并返回非 healthy，接口不抛 500

### Requirement: 删除失败重试与补偿

系统 MUST 保证增量删除收敛：删除文档时若 Milvus/ES 删除操作失败，系统 MUST 有限重试（指数退避）；重试仍失败 MUST 记录告警日志（含 doc_id、失败目标、原因）并标记待补偿，不得静默返回成功造成「PG 已 archived 而向量/索引残留」。

#### Scenario: 删除失败重试成功

- **WHEN** Milvus 删除首次失败、重试后成功
- **THEN** 系统返回成功，最终三端（PG/Milvus/ES）均无该文档残留

#### Scenario: 重试仍失败记录告警

- **WHEN** ES 删除重试仍失败
- **THEN** 系统返回业务成功（不阻断删除），但记录告警日志并标记该文档待补偿，供运维后续人工清理

### Requirement: ES 降级补索引任务持久化

在 `KB_ES_DEGRADED_KEEP_MILVUS=true` 的降级场景下，系统 MUST 持久化补索引任务状态（待补文档清单与重试进度）；进程重启后 MUST 恢复并继续补索引，不得因重启丢失导致 ES 长期缺文档。

#### Scenario: 进程重启后恢复补索引

- **WHEN** ES 降级期间服务进程重启
- **THEN** 重启后系统依据持久化状态继续补写 ES，不丢失待补文档

#### Scenario: 重试窗口内完成补索引

- **WHEN** ES 在 30min 重试窗口内恢复
- **THEN** 补索引完成，ES 与 Milvus/PG 两端一致

### Requirement: 重复删除语义约定（非幂等）

系统 MUST 对不存在的 doc_id 与已 archived（已删除）的 doc_id 统一返回 B0004（资源不存在）；重复删除不视为成功。该行为为既有契约的显式固化（对齐 document-ingest 主 spec 既有行为与 DD-03 §5.1 修订后表述）。

#### Scenario: 删除不存在的文档

- **WHEN** 管理员删除一个不存在的 doc_id
- **THEN** 系统返回错误码 B0004，不产生任何写入

#### Scenario: 重复删除已归档文档

- **WHEN** 管理员再次删除一个已 archived 的文档
- **THEN** 系统返回错误码 B0004，不产生副作用
