# 业务库 + 向量知识库强制安全规范（Text2SQL 推迟 M5+，MVP 不做）

【强制1】所有业务实体表必须存在公共字段：id、create_time、update_time、is_delete，缺一不可。例外：append-only 日志表（dialog_turns/intent_tags/audit_logs/llm_call_logs/etl_task_logs）仅含 id+create_time；dw 层 features_user 以 case_id 为 PK，仅含 update_time。
【强制2】所有更新操作自动赋值 update_time，禁止手动忽略、禁止不更新。
【强制3】禁止 select *，所有查询必须显式书写字段列表，杜绝冗余字段与数据泄露。
【强制4】列表查询必须分页/limit，禁止无条件全表查询，杜绝大数据量风险。
【强制5】Text2SQL 仅 M5+ 启用，启用时须严格白名单（表白名单、字段白名单、语法白名单）禁止越权查询；MVP 期间账单查询走 RAG + 结构化接口，不启用 Text2SQL。
【强制6】高危SQL强制拦截：drop、alter、truncate、rename、全量update、全量delete，禁止AI生成与执行。
【强制7】向量检索结果必须：相关性打分、过滤低相似度（score<0.6 判定无匹配）、去重、清洗，禁止原始数据直接喂给大模型。
【强制8】客户隐私、账单敏感字段、身份证、手机号、金额明细必须脱敏后参与LLM推理，禁止明文传输与输出。
【强制9】数据模型以 DDL 为单一事实源，Pydantic 模型对齐 DDL，CI 双向校验脚本防止字段/类型漂移。
【强制10】PostgreSQL 15 划分 public（业务）+ dw（数据中台）双 schema 同实例；Milvus 使用 BGE-M3、1024 维，collection 命名与 rag-service 对齐；Elasticsearch 8 用于 BM25 稀疏检索，bge-reranker 用于二次精排。
【AI开发硬性指令】所有数据库、向量库、Text2SQL代码必须逐条校验安全规则，高危代码直接拦截重构。
