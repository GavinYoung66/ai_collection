# 开发进度与路线图

> 本文件从 README 第 8 节拆出，集中记录项目内部进度台账（已完成清单 / 待办 / 里程碑）。
> 图例：🟢 ✅ 已完成　|　🔴 ❌ 未完成（含部分完成/进行中）　|　工作量为人天粗估

## 1. 已完成清单（✅）

### A. 功能闭环

| # | 项 | 状态 |
|---|---|---|
| 1 | data-platform 闭环参与（种子/ETL/快照两路回流/事件发布订阅） | 🟢 ✅ |
| 2 | rag-service 深做（ES BM25 + Milvus 混合检索 + rerank + 敏感词热刷新） | 🟢 ✅ |
| 3 | qc-service 闭环参与（R1~R14 规则 + LLM 复核 + 六出站/四入站全上总线） | 🟢 ✅ |
| 4 | complaint-service 深做（LangGraph 图 + MemorySaver 断点续跑 + 三 MCP 桥 + 双入口） | 🟢 ✅ |
| 5 | 四服务真实事件闭环（etl.task.done → qc.check.completed → complaint.created → complaint.result.ready） | 🟢 ✅ |
| 6 | 11/11 验收项全 PASS（run_min_loop.py 可重复执行） | 🟢 ✅ |
| 7 | call-service / case-service / review-service 脚手架（health 端口就绪） | 🟢 ✅ |

### B. 公共底座（common/）

| # | 项 | 状态 |
|---|---|---|
| 8 | LLM 统一编排 invoke_llm（选 Key → 限流 → 熔断 → 有界重试 → fallback → 降级） | 🟢 ✅ |
| 9 | MCP 可靠性层（per-server 熔断 + 30s 超时） | 🟢 ✅ |
| 10 | 事件总线 RocketMQ 唯一模式（信封白名单 + 幂等消费 + hooks 观测轨） | 🟢 ✅ |
| 11 | PII 脱敏 + CoT AES-256-GCM 加密 + 双授权解密 | 🟢 ✅ |
| 12 | 链路追踪（P0 本地 + P1 LangSmith/Langfuse 解耦 + 环境变量显式清理） | 🟢 ✅ |
| 13 | 错误码统一 `{"code","message","data","trace_id"}` + A5xxx | 🟢 ✅ |
| 14 | 鉴权下沉 common/security/authn.py（JWT 验签 + API Key 恒定时间比对） | 🟢 ✅ |
| 15 | 敏感词 AC 自动机匹配 | 🟢 ✅ |
| 16 | 提示词 YAML 配置化（dialog/qc_review/fallback_scripts） | 🟢 ✅ |
| 17 | Pydantic 结构化输出 schema | 🟢 ✅ |
| 18 | 规则兜底话术引擎（LLM 降级） | 🟢 ✅ |

### C. API 网关（北向单入口，14 项能力）

| # | 项 | 状态 |
|---|---|---|
| 19 | 单入口路径路由（四服务 + Gradio + 静态页同源托管） | 🟢 ✅ |
| 20 | 只做转发不做业务（边界约束） | 🟢 ✅ |
| 21 | upstream 舱壁隔离 + 被动熔断 + 非幂等不重试 | 🟢 ✅ |
| 22 | 全局限流 20r/s + 429 统一信封 | 🟢 ✅ |
| 23 | 流式/长连接透传 + 超时分级（3s/15s/120s） | 🟢 ✅ |
| 24 | 鉴权开关 + auth_request sidecar + 身份透传（ENABLE_AUTH 默认 false） | 🟢 ✅ |
| 25 | CORS 白名单（.env 唯一事实源，禁用 `*`） | 🟢 ✅ |
| 26 | TLS + 安全响应头（X-Frame-Options 等，TLS_ENABLED 开关） | 🟢 ✅ |
| 27 | Trace 标识透传与注入 | 🟢 ✅ |
| 28 | /metrics 指标端点（stub_status + nginx-prometheus-exporter） | 🟢 ✅ |
| 29 | JSON 访问日志（LOG_JSON 开关） | 🟢 ✅ |
| 30 | 灰度路由开关（split_clients + CANARY_WEIGHT，默认 0） | 🟢 ✅ |
| 31 | 共享网络 ai-shared-net（upstream 用容器服务名） | 🟢 ✅ |
| 32 | 配置模板化（nginx.conf.template + envsubst 白名单渲染） | 🟢 ✅ |

### D. 工程化与部署

| # | 项 | 状态 |
|---|---|---|
| 33 | Docker Compose 编排（dev/gateway/complaint/smoke 四份） | 🟢 ✅ |
| 34 | Alembic 数据库迁移（0001~0014） | 🟢 ✅ |
| 35 | Makefile（dev/test/lint/migrate/smoke） | 🟢 ✅ |
| 36 | CI workflow（complaint-service 单测 + E2E + prompt 回归 + smoke） | 🟢 ✅ |
| 37 | 单测套件（common + 四服务，1402 passed / 0 failed / 3 skipped） | 🟢 ✅ |
| 38 | Locust 压测脚本 + stub 基线准入 | 🟢 ✅ |
| 39 | 冒烟脚本（smoke_all + 各服务 smoke 脚本） | 🟢 ✅ |
| 40 | OpenSpec 规范驱动（specs + changes 归档流程） | 🟢 ✅ |

---

## 2. 待办进度条

### 🔴 P0：上生产硬门槛（不做上不了生产）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 1 | MCP 网关独立容器 | 5~8 | 🟢 ✅ | mcp-gateway-standalone change 已交付：独立服务 services/mcp-gateway（容器）接入 compose，Agent 工具调用统一经网关 MCP 协议路由 |
| 2 | CI 全服务覆盖 | 1 | 🟢 ✅ | infra-production-readiness change 已交付：ci.yml 重构为 matrix（5 服务 job + common/脚手架 job，共 6 job 并行）；强制拦截合并受 GitHub Free 私有仓无 branch protection 限制未启用（change 1.8/1.9 搁置） |
| 3 | 密钥管理（vault/sops 二选一） | 2 | 🟢 ✅ | infra-production-readiness change 已交付 sops + age：.sops.yaml 加密规则、.env 加密为入库的 .env.enc、明文不再入仓、经 sops exec-env 注入启动 |
| 4 | 数据库备份脚本（PG/Milvus/ES） | 1 | 🟢 ✅ | infra-production-readiness change 已交付：scripts/backup/ 覆盖 PG/Milvus/ES 备份与恢复，backup_all.sh 编排、非空校验、保留 7 天 |
| 5 | 服务 /metrics 端点 + Prometheus 抓取配置 + Grafana dashboard | 2~3 | 🟢 ✅ | observability-metrics-llm-usage change 已交付：8 服务统一 /metrics（svc 标签）+ Prometheus 9 job 抓取 + Grafana 内置 dashboard |
| 6 | LLM 用量可观测（llm-usage-observability） | 1~2 | 🟢 ✅ | observability-metrics-llm-usage change 已交付：llm_call 结构化日志 + llm_* 指标族（限流/熔断/fallback） |

### 🟡 P1：强烈建议（不做能跑但要付出运维代价）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 7 | 统一中台前端 MVP（登录 + 质检工作台 + 投诉研判工作台） | 15~20 | 🔴 ❌ | 当前三个演示页（原生 HTML+JS）定位是"演示与验收"非"业务用户日常工作"；无统一登录/无共享状态/无工作流 |
| 8 | CD 流水线（构建镜像 → 推仓库 → 滚动部署） | 3~5 | 🔴 ❌ | 当前部署靠手工 docker-compose up，无审计 |
| 9 | 日志采集（filebeat/vector → ELK/Loki） | 2 | 🔴 ❌ | 当前日志在容器里，容器重建即丢 |
| 10 | 告警通知（Prometheus alertmanager → 飞书/钉钉） | 1 | 🔴 ❌ | 出问题没人知道 |
| 11 | runbook 运维手册 + 应急流程 | 1~2 | 🔴 ❌ | 出问题不知道怎么处理 |
| 12 | 环境分离（dev/staging/prod 三份 .env） | 1 | 🔴 ❌ | 当前一份 .env 跑所有环境 |
| 13 | 修复 complaint-service real_transport.py 绕过 invoke_llm | 0.5 | 🔴 ❌ | 自己手写限流+重试，无 fallback/降级/统一返回结构；影响 LLM 用量统计口径 |
| 14 | call-service / case-service / review-service 真实实现 | — | 🔴 ❌ | 当前脚手架，M2/M4 里程碑 |

### 🟢 P2：可延后（当前无需求或规模未到）

| # | 项 | 工作量（人天） | 状态 | 说明 |
|---|---|---|---|---|
| 15 | 知识库管理台（替换 Gradio 演示页） | 4~6 | 🔴 ❌ | 文档上传→向量化→检索测试→敏感词管理 |
| 16 | 运营监控台（接 Prometheus + 事件总线） | 4~6 | 🔴 ❌ | LLM 用量/限流/熔断状态/事件堆积/慢查询 |
| 17 | 审计与合规台（含 CoT 双授权解密工作流） | 4~6 | 🔴 ❌ | 操作日志/CoT 解密双授权/PII 脱敏数据查看 |
| 18 | 通知中心（SSE + 事件订阅） | 2~3 | 🔴 ❌ | 事件总线关键事件前端实时推送 |
| 19 | K8s manifests | — | 🔴 ❌ | 500 人内部单租户，docker-compose 够用；要上 k8s 再做 |
| 20 | 灰度真启用（CANARY_WEIGHT > 0） | — | 🔴 ❌ | 单版本，开关已备好但无需求 |
| 21 | LLM 独立网关（llm-gateway-standalone） | 2 | 🔴 ❌ | common 已有完整 invoke_llm 编排器，独立化收益当前用不上；暂缓 |
| 22 | 队列升级（Redis List → Redis Stream 消费组） | — | 🔴 ❌ | ACK 不丢任务/多 worker 竞争/失败重试/DLQ/对账补偿；待真丢任务后再做 |
| 23 | 配置中心（Apollo/Nacos） | — | 🔴 ❌ | .env 单一事实源够用 |
| 24 | DR 灾备 | — | 🔴 ❌ | 内部系统，RPO/RTO 容忍度高时可不急 |

---

## 3. 里程碑对照

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 基础底座 | 🟢 ✅ | common/ + 网关 + 事件总线 + 数据库迁移 |
| M1 质检系统 | 🟢 ✅ | qc-service 闭环参与 |
| M2 外呼/分案 | 🔴 ❌ | call-service / case-service 脚手架 |
| M3 投诉研判 | 🟢 ✅ | complaint-service 深做 + 事件闭环 |
| M3.5 投诉 V2 | 🟢 ✅ | real-llm + 事件化 + 图状态机修复 |
| M4 复盘 | 🔴 ❌ | review-service 脚手架 |
| 生产上线 | 🟢 ✅ | P0 六项硬门槛已清零（2026-10-10 infra-production-readiness + observability-metrics-llm-usage 两 change 归档） |
