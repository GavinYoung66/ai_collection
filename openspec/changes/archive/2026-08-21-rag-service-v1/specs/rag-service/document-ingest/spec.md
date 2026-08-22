## Purpose

支持催收业务知识文档（PDF/DOCX/MD/TXT）上传后自动解析、清洗、分块、向量化并写入 Milvus 和 Elasticsearch 双索引，支持单文档增量删除和重新入库，禁止全量重建。

## ADDED Requirements

### 接口鉴权通用约定

- 身份凭证：`Authorization: Bearer <JWT>`，JWT 由公共 `common.security.rbac` 签发/校验，不在 rag‑service 内部重复实现鉴权逻辑；
- 本 capability 各接口权限要求（角色比较大小写不敏感，seed 小写 `admin`/`supervisor` 与契约大写 `ADMIN`/`SUPERVISOR` 均匹配）：

| 接口                                        | 权限             | 校验方式 |
| :------------------------------------------ | :--------------- | :------- |
| 上传 POST /kb/documents                     | ADMIN/SUPERVISOR | 校验     |
| 列表 GET /kb/documents                      | ALL              | 跳过     |
| 详情 GET /kb/documents/{doc_id}             | ALL              | 跳过     |
| 删除 DELETE /kb/documents/{doc_id}          | ADMIN/SUPERVISOR | 校验     |
| reindex POST /kb/documents/{doc_id}/reindex | ADMIN/SUPERVISOR | 校验     |

- 未携带身份凭证或凭证无效：返回 HTTP 401，统一错误码 `B0002`（请先登录）；
- 凭证有效但角色权限不足：返回 HTTP 403，统一错误码 `B0003`（无操作权限）；
- 权限校验抽离为独立公共组件（`app/services/require_role.py`），在路由层完成拦截，先校验权限再执行业务逻辑；
- 内部接口 `GET /sensitive-words/current.dict` 完全不接入权限校验（检索接口权限为 ALL，归属 hybrid‑search capability）。

### 接口限流通用约定

- 限流存储介质：Redis（DB2），按用户维度限流判断（优先 `X-User-Id` 请求头，缺失时回退客户端 IP）；
- 本 capability 各接口限流阈值（固定窗口 60s）：

| 接口                                        | 限流阈值  |
| :------------------------------------------ | :-------- |
| 上传 POST /kb/documents                     | 20次/分钟 |
| 列表 GET /kb/documents                      | 60次/分钟 |
| 详情 GET /kb/documents/{doc_id}             | 60次/分钟 |
| 删除 DELETE /kb/documents/{doc_id}          | 20次/分钟 |
| reindex POST /kb/documents/{doc_id}/reindex | 10次/分钟 |

- 请求超过阈值：返回 HTTP 429，错误码 `B0006`，响应体遵循项目统一响应包格式 `{code, message, data, trace_id}`；
- 限流逻辑抽离为独立公共组件（`app/services/rate_limit.py`），在路由层完成拦截，先限流校验后执行业务逻辑；
- 敏感词只读接口为内部接口，不挂载限流（检索接口 200次/分钟 归属 hybrid‑search capability）。

### Requirement: 文档上传与自动入库

系统 SHALL 接受 PDF、DOCX、MD、TXT 格式文件上传，自动执行：解析提取文本→清洗（去空行、多余空格、页眉页脚）→分块→向量化（1024dim）→写入 Milvus 向量库和 Elasticsearch BM25 索引→写入 PostgreSQL kb_chunks 表→更新文档状态为 active。

#### Scenario: 上传 TXT 文档成功

- **WHEN** 管理员上传一个 TXT 文件并指定 category=regulation
- **THEN** 系统返回 doc_id、chunks_count > 0、status=active，且 kb_chunks 表有对应记录，Milvus 和 ES 可检索到该文档内容

#### Scenario: 上传 PDF 文档成功

- **WHEN** 管理员上传一个 PDF 文件
- **THEN** 系统提取文本（过滤页眉页脚），分块后向量化写入双索引，返回 status=active

#### Scenario: 上传不支持格式

- **WHEN** 管理员上传一个 .exe 文件
- **THEN** 系统返回错误码 A3001（文档解析失败），HTTP 200，统一响应包 code 不为 "0"

### Requirement: 文档状态生命周期管理

系统 MUST 管理文档状态流转：processing（入库中）→ active（可用）或 failed（入库失败）。archived 表示已归档删除。

#### Scenario: 入库成功状态流转

- **WHEN** 文档上传并成功完成全链路入库
- **THEN** 文档状态从 processing 变为 active

#### Scenario: 入库失败状态流转

- **WHEN** 向量化服务不可用导致入库失败
- **THEN** 文档状态变为 failed，并记录错误日志

### Requirement: 增量删除

系统 SHALL 支持按 doc_id 删除单个文档：删除 PostgreSQL kb_chunks 对应记录、删除 Milvus 中该文档的所有向量、删除 ES 中该文档的所有索引文档、更新文档状态为 archived。禁止全量重建知识库。

#### Scenario: 删除文档后索引清理

- **WHEN** 管理员删除一个已入库的文档
- **THEN** Milvus 搜索该文档内容返回空，ES 搜索该文档内容返回空，kb_chunks 表该文档记录被软删除，kb_documents 状态变为 archived

### Requirement: 重新解析入库

系统 SHALL 支持对已入库文档执行重新解析：先删除旧索引（Milvus + ES + PG chunks），再重新走完整入库流程。

#### Scenario: 重新入库后内容更新

- **WHEN** 管理员对文档执行 reindex 操作
- **THEN** 旧的 chunks 和向量被清除，新的 chunks 和向量被写入，文档状态变为 active

### Requirement: 分类隔离

系统 MUST 通过 category 字段（regulation/script/case/other）实现知识分类，文档上传时必填 category，检索时可按 category 过滤。

#### Scenario: 按分类过滤检索

- **WHEN** 检索时指定 category=regulation
- **THEN** 返回结果只包含 category=regulation 的文档片段，不返回 script/case/other 类片段

### Requirement: 脏数据过滤

系统 MUST 过滤无效分块：字符长度小于 10 的片段直接丢弃，不写入索引。

#### Scenario: 短片段被过滤

- **WHEN** 文档分块后产生一个长度为 5 个字符的片段
- **THEN** 该片段不写入 Milvus、ES 和 kb_chunks 表

### 文档入库链路异常行为契约（PG‑Milvus‑ES多存储写入）

> 当前V1原始spec未定义多存储部分失败的处理逻辑，代码实现遵循以下约定：

1. 入库流程中，若PG数据库写入成功，但后续Milvus/ES向量索引写入发生异常：
   - 将`kb_documents.status`更新为`failed`；
   - 对已经写入Milvus/ES的部分索引执行回滚删除；
   - 返回业务错误码`A3003`，提示文档入库失败；
2. 禁止出现PG状态为active，但向量库索引缺失的脏数据状态。
3. 仅PG失败场景：不会向Milvus、ES执行写入操作。

### 输入参数边界约束（入库侧）

1. 上传空文件、损坏加密PDF：解析阶段捕获异常，文档status置为`failed`，返回`A3001`。
