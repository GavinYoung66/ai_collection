## Why

架构评审暴露：文档叙事与代码落地不完全一致（RAG 归属、容量口径、脚手架完备假象）；投诉→`rag_kb` MCP→rag-service 真路径未闭合（Mock 默认、gateway standalone `NotImplemented`）；跨服务重复维护 LLM 重试/熔断与 Guardrails。在 `platform-integration-boundaries` 与 complaint v4 已收口边界/transport 的基础上，本 change 做项目级叙事统一、集成闭合与公共能力抽取，使 Demo 口径可面试自洽，并为后 5 个子系统预留**可检查的**扩展点（清单，非填肉）。

## What Changes

- **叙事与口径**：统一「RAG=独立业务服务(8002)、非 common 库」；对外口径「2 深切片 + 5 占位」；容量模型表对齐（外呼并发 vs 质检吞吐）；Demo 路径强制 `run_graph=true` + human_review。
- **启动防护**：`USE_MOCK_*=false` 时 MCP/LLM 必须探活或 fail-fast（DEBT-04）。
- **集成闭合**：`rag_kb`→rag HTTP 桥接；MCP gateway standalone；compose 依赖语义；未用 MCP 工具标 reserved。
- **平台抽取**：`common` LLM reliability + Guardrails；rag 与 complaint **均**组合调用；明确 `chat_json` 遗留入口与 Redis degrade 主语义。
- **债务清理**：prompt 迁出 transport；URL/Key 配置化；`change_me` 校验；settings 显式读法；处理 `rag-service/common/` 阴影目录（DEBT-03）。
- **扩展点（轻量）**：新增子系统接入清单文档（DEP-05 影子交付，不做 data-platform API）。
- **文档**：`docs/complaint-service/external-deps/` Mock vs Real；废弃空 change `complaint-service-v5` 说明。

### Non-Goals

- 不实现 call/qc/case/review/data-platform **业务功能**（health 占位可接受）；**不做 ARCH-04 / COMP-07**（Batch-E 填肉 → V5.1+）
- 不重做 `platform-integration-boundaries` 已完成的 transport/Settings 瘦身（仅扩展）
- 不做完整 KMS、Nacos PromptOps、多租户、k8s；不替换 Milvus/ES；不引入 Kafka（PLAT-03 仅 design 可提接口）
- **Batch-D 全量不做**：`ENG-02` Metrics、`ENG-07` safe_message、`COMP-01` 脱敏全路径清单、`COMP-02` 审计保留/防篡改 → **V5.1**。本 change 仅 stretch 最小切片 `ENG-01`（sink 形状），**不代表可观测/审计完成**
- **Batch-G 整批暂缓**：TECH-03~06、PLAT-03~04、ENG-03~06、ENG-08、COMP-05~06
- 不做 ENG-03~08「全量」可观测；stretch ENG-01 ≠ 完成 D
- 不把「月 10 万工单 / 知识库 10 万篇」写成已验证规模

### Issue ID coverage（对齐原 A–G 字母）

| 原 Batch | IDs | 本 change 处理 |
|----------|-----|----------------|
| **A** 叙事 | ARCH-01, ARCH-06, TECH-01, TECH-02, COMP-04 | **P0 做** |
| **B** 集成 | DEP-01, DEP-02, DEP-03, **DEBT-04** | **P0 做** |
| **C** 平台 | ARCH-02, ARCH-03, PLAT-01, PLAT-02 | **P0 做** |
| **F** 债务（并入本 change 执行，字母仍属 F） | DEBT-01, DEBT-02, **DEBT-03**, DEBT-05, ARCH-05, DEP-04 | **P0 做**（与 B/C 任务交错） |
| **D** 可观测 | ENG-01, ENG-02, ENG-07, COMP-01, COMP-02 | **仅 stretch ENG-01**；其余 V5.1 |
| **E** 中台 | ARCH-04, DEP-05, COMP-07 | ARCH-04/COMP-07 **不做**；DEP-05 → **轻量清单文档** |
| **G** 暂缓 | TECH-03~06 等 | **不做** |
| 加分（非原 A–G 必选） | COMP-03 evidence_refs | **stretch only**，面试加分，非 D 完成 |

## Capabilities

### New Capabilities

- `platform/architecture-narrative`: 口径、容量模型、脚手架表述、RAG 归属、新服务接入清单（文档级）
- `platform/llm-reliability`: common reliability；禁止第三条裸 HTTP；遗留 `chat_json` 定位；Redis degrade 主语义
- `platform/guardrails`: common 前后置护栏最小行为
- `complaint-service/mcp-real-path`: gateway 真路径、Mock、fail-fast、compose
- `complaint-service/rag-kb-bridge`: rag_kb → rag HTTP 桥接
- `complaint-service/v5-demo-acceptance`: V5 验收门禁（含 stretch 标注）

### Modified Capabilities

- `platform/integration-boundaries`: 真 MCP 路径 + reliability/guardrails 分层增量

## Impact

- **文档**：step3 `01`/`02`、step1 容量交叉说明、README、`docs/complaint-service/*`、`docs/platform/new-service-checklist.md`（apply 时创建）
- **common**：`reliability`、`guardrails`、导出与单测
- **complaint / rag**：gateway、bridge、L4/answer 组合调用、prompt 迁出、占位校验
- **目录卫生**：`services/rag-service/common/` 删除或禁止；空 `openspec/changes/complaint-service-v5` 废弃说明
- **兼容**：默认 Mock Demo 与 v4 复核/回电不得回归
