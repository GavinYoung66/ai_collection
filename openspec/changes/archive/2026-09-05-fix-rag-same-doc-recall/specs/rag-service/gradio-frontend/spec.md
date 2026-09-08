## ADDED Requirements

### Requirement: 检索请求使用本轮原句

Gradio 问答发往 `POST /kb/search` 的 `query` MUST 等于用户本轮输入原文（去首尾空白后），MUST NOT 把最近若干轮问题用空格拼接成一条检索 query。调试区可以展示历史轮次，但展示用字符串 MUST NOT 被当作检索 query。后端 query rewrite 开关与行为不在本 Requirement 范围内。

#### Scenario: 同 session 连续两问不拼接

- **GIVEN** 同一会话已问「RAG多模态全局知识库系统 角色定位是什么」
- **WHEN** 用户再问「RAG多模态全局知识库系统 这个 技术类别 有那些？」
- **THEN** 后端收到的 query / `kb_search_logs.query` / `query_original` 等于第二问原文，不得包含「角色定位」

#### Scenario: 调试区仍可见历史

- **WHEN** 用户连续提问
- **THEN** 页面可展示历史轮次或「未拼接历史」说明，且实际请求 query 仍为本轮原句

### Requirement: 同名已入库时前端阻止再传

Gradio 上传前 MUST 检测同名 `status=active`（未删除）文档；若存在 MUST 阻止或强烈提示「同名已入库，请先删除或改文件名」，MUST NOT 在无提示下再发 POST。同名 `processing` 阻止/强提示保持不变。

processing 同名检测 MUST 穷尽匹配：不得只读取 `page_size=100` 的第一页后漏检。允许按 title 过滤、翻页直至穷尽，或调用后端已有/新增的按 title 查询。

#### Scenario: 同名 active 再点上传

- **GIVEN** 列表中已有同名文件 status=active
- **WHEN** 用户再次选择同名文件点击上传
- **THEN** 页面阻止或强烈提示；不得静默发出第二条入库请求

#### Scenario: 同名 processing 不在第一页仍能拦住

- **GIVEN** status=processing 的同名文档不在列表第一页（或超过单页 100 条）
- **WHEN** 用户上传同名文件
- **THEN** 页面仍阻止或强烈提示；不得因只扫第一页而漏拦
