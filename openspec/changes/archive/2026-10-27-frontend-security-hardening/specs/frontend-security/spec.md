# Frontend Security Hardening — Delta Spec

**Change:** `frontend-security-hardening`
**Date:** 2026-10-27
**Input:** security-best-practices 技能前端审查报告（`security_best_practices_report.md`）
**基线：** vitest 39 文件 349 用例全绿；auth 刷新持久化正常

## Scope（严格边界）

### 允许修改
- `frontend/src/**` — 前端业务代码
- `deploy/gateway/**` — 网关部署层配置（属部署层，非后端业务代码；服务于前端同源托管与安全头）
- `.github/workflows/ci.yml` — CI 门禁配置（npm audit 步骤合并到既有 frontend job，未单独新建 workflow 文件）
- `openspec/**` — 规范文件

### 禁止修改
- ❌ `services/**` — 所有后端业务代码（complaint/qc/rag/auth-sidecar/data-platform）
- ❌ `common/**` — 后端公共库（含 resilience/security 等）
- ❌ 数据库 schema / migrations

## 变更清单（5 个安全问题的前端侧可实施项）

### REQ-1：网关补 CSP 安全头（中危 VUE-AUTH-001 缓解）

**描述：** `deploy/gateway/server_common.conf.template` 补 `Content-Security-Policy` 响应头，限制脚本/样式/图片/connect-src 的加载来源。

**验收标准：**
- 浏览器 DevTools Console 无 CSP 违规告警
- 默认策略：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' http://localhost:8080`（dev 环境）
- 生产环境 `connect-src` 仅允许同源（移除 `http://localhost:8080`）

### REQ-2：auth.ts 换 sessionStorage 存储 token（中危 VUE-AUTH-001 缓解）

**描述：** `frontend/src/app/stores/auth.ts` 的 `STORAGE_KEYS.TOKEN` 存储从 `localStorage` 换为 `sessionStorage`。会话结束自动清除，降低 XSS 接管爆炸半径。

**验收标准：**
- 刷新页面不丢登录态（sessionStorage 在同 tab 刷新后保留）
- 关闭 tab 后重开需重新登录（会话级清除）
- vitest auth store 相关用例通过
- **基线对比已记录**：修改前 `localStorage.setItem` 持久化，修改后 `sessionStorage.setItem` 会话级

### REQ-3：两处外链 :href 加协议白名单校验（低危 LOW-1）

**描述：** `frontend/src/modules/qc/components/TraceTimeline.vue` 和 `frontend/src/modules/complaint/components/TraceTimeline.vue` 的 langsmith_url 渲染前校验协议为 `http:` 或 `https:`。

**验收标准：**
- `javascript:` / `data:` / `//evil.com` 等非法协议被拦截（渲染为空或不渲染）
- 合法 http/https 链接正常渲染
- 已有 `rel="noopener noreferrer"` 保留

### REQ-4：CI 加 npm audit 门禁（低危 LOW-3）

**描述：** `.github/workflows/ci.yml` 的 `frontend` job 中，在前端测试阶段后增加 `npm audit --audit-level=high`，有高危即 fail build。（实现说明：原规划新建独立 frontend.yml，实际合并到统一 ci.yml，功能等效。）

**验收标准：**
- 当前 `npm audit --audit-level=high` 返回 0（无高危）
- 新增 workflow 文件 lint/format 合规

### REQ-5：后端依赖项的前端预留钩子（信息级，不实现逻辑）

**描述：** 三处极简空函数+注释预留，当前无后端配合时为空实现，等后端就绪后开启。

| 编号 | 位置 | 预留内容 | 注释 |
|---|---|---|---|
| REQ-5a | `frontend/src/shared/request.ts` | 导出 `registerErrorReporter(fn: (err: RequestError) => void)`，内部维护数组，无调用方时钩子为空 | "后端审计接口 POST /api/v1/audit/frontend-error 就绪时，在 main.ts 调用 registerErrorReporter" |
| REQ-5b | `frontend/src/app/pages/Login.vue` | 错误分支加 `else if (e.code === 'B0011') { errorMsg.value = '账号已锁定，请15分钟后重试' }` — 后端返回 **B0011**（账号已锁定）而非 B0005（complaint-service 已用 B0005 做"同工单重复提交"，两码隔离避免语义冲突） | "后端已实现：auth-sidecar Redis 计数器阈值 5 次触发 status=locked，返回 HTTP 423+B0011；complaint B0005 零影响" |
| REQ-5c | `frontend/src/app/stores/auth.ts` + `frontend/src/shared/request.ts` | auth.ts 加 `refresh_token: ''` 字段；request.ts 401 处理注释预留刷新调用位置 | "后端 Refresh Token 接口就绪时启用自动重试" |

**验收标准：**
- 三处预留仅加注释+空分支/空字段，无有效逻辑变更
- vitest 全量通过，无新增失败
- 代码审查确认：无半成品调用逻辑

## Out of Scope（明确排除）

- Cookie 化 token（需后端 auth 服务改发 Set-Cookie 头）
- 审计日志落库（需后端 auth.py 写入 AuditLog 模型）
- 登录失败计数 Redis 实现（需后端 auth.py 加计数器+锁定）
- Refresh Token 签发/校验（需后端 auth 路由）
- npm audit 全量依赖升级（本次仅加门禁，不升级现有依赖）
- Docker 容器非 root 用户（后端镜像修改，属部署层但超出本次"仅前端"边界）
