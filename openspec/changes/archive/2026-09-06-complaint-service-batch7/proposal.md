# Proposal: complaint-service-batch7

## Why

批次6 已交付完整 LangGraph 编排（State + 节点 + 整图编译 + interrupt），但系统尚无对外 API 入口与安全护栏。本批交付模块7（API 接口层）与模块10（安全护栏），使投诉研判系统具备可调用的 REST 接口（12 路由）与合规安全能力（脱敏/审计/权限/L4 后置校验），为批次8 业务服务层与批次9 灰度降级奠定接入基础。

## What Changes

**模块7 API 接口层（7.1～7.5）**
- 7.1 `app/api/routes/complaints.py` + schemas：POST/GET /complaints、GET /{id}、POST /{id}/judge、GET /{id}/status、GET /{id}/verdict、GET /{id}/agent-logs、PUT /{id}/review；verdict 四值无 normal
- 7.2 `app/api/routes/stats.py`：GET /complaints/stats、GET /complaints/consistency
- 7.3 `app/api/routes/mode.py`：PUT /complaints/mode；失败回滚返回 **A5014**（见 design：与 step8 字面 A5013 冲突的处理）；写 Redis `csmp:mode:*`（FakeRedis）
- 7.4 合并进 complaints.py：PUT /complaints/{id}/close；stub 发布 complaint.closed
- 7.5 挂载：`main.py` **先** include stats/mode，**再** complaints（避免 `/{id}` 吞掉 `stats`）；注册 A5001～A5013 + **增补 A5014**；统一响应包
- **judge 默认 stub**：不默认同步跑全图 interrupt（防 TestClient 挂死）

**模块10 安全护栏（10.1～10.4）**
- 10.1 补全 MCP 二次脱敏告警+审计（`mask_text` 已部分就位，**禁止整段重写 client**）
- 10.2 `mask_before_persist` + provider/节点关键路径钩子
- 10.3 `audit_service.py` RBAC + audit_logs
- 10.4 `guardrails_postcheck.py`：按 PRD 12.2 七条红线**逐条标注本批范围**（见 design D8）

## Non-Goals

- 不实现批次8 完整业务服务（工单会话编排、复核 SLA、回调、监控全量）——API 接薄 stub
- 不实现批次9 灰度降级矩阵 / 限流全量（7.3 仅写 Redis key）
- 不接真实 Pub-Sub / 真实 PG 落库
- `POST /{id}/judge` **本批默认不**同步跑完整 interrupt 图（可选测试钩子另述）
- 路径一律 `services/complaint-service/`；禁止仓库根旁路树

## Capabilities

### New Capabilities
- `complaint-service/api-layer`: 12 REST 路由 + 统一响应 + 错误码
- `complaint-service/security-guardrails`: 脱敏 + RBAC 审计 + L4 后置校验

## Impact

- **新增**：routes/schemas、薄 `complaint_service` stub、`audit_service` / `guardrails_postcheck` / `security.mask_before_persist`、EventPublisher stub
- **修改**：`main.py` 挂载顺序；`mcp/client.py` 补告警+审计；`llm/provider.py` 脱敏钩子；`common/errors/codes.py` **增补 A5014**
- **依赖**：批次6 graph/State；`common.security.*` / `common.errors.*`
