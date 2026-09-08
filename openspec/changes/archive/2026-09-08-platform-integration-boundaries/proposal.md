## Why

七大子系统的目标架构（代码解耦 + 共享 `common/`）合理，但落地出现三处分叉：step3 调用矩阵写 complaint→rag/qc 为 HTTP，投诉实现走 MCP；`common` Settings 堆满 RAG 专用 `KB_*`；强制「统一 LLM 工具类」与 common/rag/complaint 三套 Provider 并存。现在只深做了 RAG + 投诉，若不先统一叙事与边界，后续五个脚手架服务会继续复制错误集成风格。本变更对齐「统一 = 边界清楚 + 该共用的共用 + 叙事一致」，小步收敛，不推倒已跑通的投诉 Agent。

## What Changes

- **集成准则定稿（文档 + 编排）**：明确 Agent 工具 = MCP；服务管理 API = HTTP；异步 = 事件。MCP Server 可内部再 HTTP 调下游。更新 step3 调用矩阵与 `01_arch_constraint` 相关强制条款措辞。
- **dev compose**：Mock MCP 阶段去掉 `complaint-service` 对 `rag-service` 的硬 `depends_on`（演示不拉 RAG 全家桶）。
- **Settings 瘦身**：`KB_*` 归属迁到 rag 侧 Settings；`common` 仅保留跨服务平台项；投诉继续 `ComplaintSettings`。
- **LLM Transport 下沉**：`common.llm` 统一 transport + 超时/重试/熔断/Key 解析；rag/complaint Provider 变薄，保留各自业务编排（结构化 schema / 带引用回答）。
- **非目标 / 不做**：**不**把投诉改回纯 HTTP 取证；**不**本变更实现真 MCP Server；**不**拆独立 PG 实例；**不**合并成单一上帝 Provider 类。

## Capabilities

### New Capabilities

- `platform/integration-boundaries`: 平台级集成分层准则、配置边界与 LLM 调用通道边界（跨服务规范，不限单一业务服务）。

### Modified Capabilities

- （无）本变更不修改 complaint/rag 既有业务需求句；行为级约束落在新能力 `platform/integration-boundaries`。架构文档与强制规范措辞同步更新作为配套交付。

## Impact

- **文档**：`requirements_document/step3_架构设计/01_*.md`、`02_模块调用关系*.md`；`openspec/specs/01_arch_constraint.md`；可选 `docs/complaint-service/external-deps/README.md`。
- **编排**：`docker-compose.dev.yml`（complaint `depends_on`）。
- **配置代码**：`common/config/settings.py`；rag-service 配置入口；`.env.example` 分段注释。
- **LLM 代码**：新增/扩展 `common/llm` transport；`services/complaint-service/app/llm/*`；`services/rag-service/app/services/llm_providers/*`。
- **兼容**：默认运行时行为保持（Mock MCP、现有 API）；无对外 REST **BREAKING**。compose 依赖变轻可能改变「一键 up complaint 却拉起 milvus」的旧习惯（预期行为）。
