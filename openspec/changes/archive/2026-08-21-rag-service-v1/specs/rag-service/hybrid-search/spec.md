## Purpose

提供混合检索能力：Milvus 向量语义召回 + Elasticsearch BM25 关键词召回，通过 RRF 算法融合，经 Reranker 精排降噪，低于阈值时触发兜底策略，返回带溯源信息的检索结果。

## ADDED Requirements

### 接口鉴权通用约定

- 身份凭证：`Authorization: Bearer <JWT>`，JWT 由公共 `common.security.rbac` 签发/校验，不在 rag‑service 内部重复实现鉴权逻辑；
- 本 capability 各接口权限要求（角色比较大小写不敏感，seed 小写 `admin`/`supervisor` 与契约大写 `ADMIN`/`SUPERVISOR` 均匹配）：

| 接口                                     | 权限     | 校验方式                   |
| :--------------------------------------- | :------- | :------------------------- |
| 检索 GET /kb/search                      | ALL      | 跳过                       |
| 敏感词 GET /sensitive-words/current.dict | INTERNAL | 跳过（完全不接入权限校验） |

- 未携带身份凭证或凭证无效：返回 HTTP 401，统一错误码 `B0002`（请先登录）；
- 凭证有效但角色权限不足：返回 HTTP 403，统一错误码 `B0003`（无操作权限）；
- 权限校验抽离为独立公共组件（`app/services/require_role.py`），在路由层完成拦截，先校验权限再执行业务逻辑。

### Requirement: 双路并行召回

系统 SHALL 并行执行向量语义检索（Milvus，BGE-M3 1024dim，top20）和 BM25 关键词检索（Elasticsearch，top20），两路检索均携带 category 过滤条件，合并后按 chunk_id 去重。

#### Scenario: 双路均返回结果

- **WHEN** 用户查询"逾期协商还款"，且知识库中有相关文档
- **THEN** Milvus 和 ES 各返回最多 20 条结果，合并去重后送入 RRF 融合

#### Scenario: 一路无结果另一路有结果

- **WHEN** 向量检索无匹配但 BM25 有精确匹配
- **THEN** 系统使用 BM25 单路结果继续流程，degraded 标记为 true

### Requirement: RRF 融合

系统 MUST 使用 RRF（Reciprocal Rank Fusion）算法融合双路结果：score = 1/(k + rank_dense) + 1/(k + rank_sparse)，k 默认 60，按 chunk_id 去重保留高分。

#### Scenario: 两路检索有重叠结果

- **WHEN** 同一 chunk 在向量检索排第 2、BM25 检索排第 1
- **THEN** 该 chunk 的 RRF score = 1/(60+2) + 1/(60+1) ≈ 0.0337，排名上升

### Requirement: Reranker 重排序

系统 SHALL 调用 rerank-service:8101 对 RRF 融合后的候选集进行精排，返回 rerank_score。Rerank 失败时降级使用 RRF score 排序，degraded 标记为 true。

#### Scenario: Rerank 成功

- **WHEN** Reranker 服务正常
- **THEN** 候选集按 rerank_score 重新排序，与原始 RRF score 排序不同

#### Scenario: Rerank 服务不可用降级

- **WHEN** rerank-service 超时或连接失败
- **THEN** 系统使用 RRF score 排序，degraded=true，不阻断检索流程

### Requirement: 低分兜底策略

系统 MUST 在 Top1 rerank_score < 0.35 时判定无有效资料，不调用 LLM，返回固定兜底文案。兜底响应 results 为空数组，message 字段填写兜底提示。

#### Scenario: 触发兜底

- **WHEN** 检索结果 Top1 的 rerank_score 为 0.2
- **THEN** 返回 results=[]，message="知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"

#### Scenario: 未触发兜底

- **WHEN** 检索结果 Top1 的 rerank_score 为 0.8
- **THEN** 返回 top_k 条结果，message 为 null

### Requirement: 溯源输出

系统 MUST 在检索结果中附带溯源信息：doc_id、title（文档名）、chunk_index、content（原文片段）、score（RRF 融合分数）、rerank_score。

#### Scenario: 检索结果包含溯源

- **WHEN** 用户执行检索并返回结果
- **THEN** 每条结果包含 doc_id、title、chunk_index、content、score、rerank_score 字段

### Requirement: 降级策略

系统 MUST 支持以下降级场景：Milvus 宕机时降级为 ES-only 检索；ES 宕机时降级为 Milvus-only 检索；双侧宕机返回错误码 A3005；任何降级场景 degraded 标记为 true。

#### Scenario: Milvus 宕机降级

- **WHEN** Milvus 连接失败
- **THEN** 系统仅使用 ES BM25 检索，degraded=true，不返回错误

#### Scenario: ES 宕机降级

- **WHEN** Elasticsearch 连接失败
- **THEN** 系统仅使用 Milvus 向量检索，degraded=true，不返回错误

#### Scenario: 双侧宕机

- **WHEN** Milvus 和 ES 均不可用
- **THEN** 系统返回错误码 A3005，不返回结果

### Requirement: 统一响应格式

所有检索接口 MUST 返回统一响应包 `{"code":"0","message":str,"data":any|null,"trace_id":str}`，code 为字符串 "0" 表示成功，trace_id 全链路透传。

#### Scenario: 检索成功响应

- **WHEN** 检索正常返回结果
- **THEN** 响应 code="0"，data 包含 results、degraded、query_original、trace_id 字段

### Redis查询缓存补充契约

- 缓存key生成规则：`rag:cache:query:{md5_hash}`，md5由 `query + category + top_k` 拼接字符串计算得出。
- 缓存失效策略：文档新增、删除、reindex重建索引时，失效对应category下全部查询缓存；**禁止使用Redis `KEYS`高危命令**。
- 实现方式：使用Redis Set集合维护 `rag:cache:keys:{category}`，每个category集合保存该分类下全部缓存key；清理缓存时读取集合内key进行删除（category为空时归入全局桶 `rag:cache:keys:`，失效时同时清理全局桶）。
- 容错降级：Redis发生连接失败、超时、报错时，不抛出业务异常，静默绕过缓存逻辑，执行原生检索流程，不影响接口可用性。
- 开关控制：读取配置`KB_CACHE_ENABLED`，开关关闭时完全跳过整套缓存逻辑；TTL使用配置项`KB_CACHE_TTL`。
- 缓存仅存储对外API序列化后的响应dict对象，禁止缓存内部service/pydantic实体对象。

### API限流补充契约

- 限流存储介质：Redis（DB2）；
- 限流统计维度：按用户维度计数（优先 `X-User-Id` 请求头，缺失时回退客户端 IP）；
- 各接口限流配置（固定窗口 60s）：上传/删除 20/min，列表/详情查询 60/min，检索接口 200/min；
- 敏感词只读接口为内部接口，不挂载限流；
- 触发限流后：HTTP 429 状态码，返回统一响应包：

```json
{
  "code": "B0006",
  "message": "请求过于频繁，请稍后再试",
  "data": null,
  "trace_id": "xxx"
}
```

### 输入参数边界约束（检索侧）

1. `top_k` 参数：不允许小于等于0，设置上限最大值100，非法入参返回`B0001`参数校验错误；
2. `category`：仅允许 `regulation / script / case / other`，传入其他非法值返回`B0001`。

### 敏感词只读接口补充契约

> 归属模块：`hybrid-search/spec.md`（原 design.md「## 4 敏感词只读接口补充契约」归档迁移）

1. 新增 `GET /sensitive-words/current.dict`，标记为 `INTERNAL` 内部接口，不做权限校验；
2. 读取项目内置敏感词字典文件（`rag-service/data/sensitive_words.dict`，每行一个词），按原始文本原样返回，`Content-Type: text/plain`，**不包装 JSON 结构体**；
3. 字典文件不存在 / IO 读取失败：直接返回 HTTP 404，不抛 500、不打印堆栈到全局、不触发全局异常处理器；
4. 只做词典读取输出，严禁实现敏感词拦截/过滤/校验逻辑，不改动上传、检索、文档处理任何业务流程；
5. 接口内部全部异常就地捕获，仅影响本接口，不影响其他接口正常运行。
