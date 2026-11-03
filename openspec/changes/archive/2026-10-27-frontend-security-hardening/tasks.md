# Tasks — frontend-security-hardening

**Schema:** spec-driven
**Change:** frontend-security-hardening
**Specs:** specs/frontend-security/spec.md

## 执行批次

### 批次 A：网关 CSP 头（REQ-1）

- [ ] A1. 在 `deploy/gateway/server_common.conf.template` 的安全头组新增 `add_header Content-Security-Policy`
- [ ] A2. dev 环境 `connect-src` 含 `http://localhost:8080`；生产环境仅 `'self'`
- [ ] A3. `docker compose up --build` 重启网关，curl 验证响应头存在且无 CSP 违规
- [ ] A4. vitest 全量通过（确认 CSP 头不影响前端功能）

### 批次 B：auth.ts sessionStorage（REQ-2）

- [ ] B1. 改 `STORAGE_KEYS.TOKEN` 的 get/set/remove 为 sessionStorage
- [ ] B2. 刷新页面验证登录态保留（sessionStorage 同 tab 刷新保留）
- [ ] B3. 关闭 tab 重开验证需重新登录
- [ ] B4. vitest auth store 相关用例通过

### 批次 C：外链协议白名单（REQ-3）

- [ ] C1. 在两个 TraceTimeline.vue 的 langsmith_url 渲染前加 `isSafeHref(url)` 校验函数
- [ ] C2. 校验函数：`new URL(url).protocol` 必须为 `http:` 或 `https:`
- [ ] C3. 浏览器验证：合法 http/https 链接渲染正常；`javascript:` / `//evil.com` 被拦截
- [ ] C4. vitest 全量通过

### 批次 D：npm audit 门禁（REQ-4）

- [ ] D1. 手动跑 `npm audit --audit-level=high` 确认基线（当前应为 0 高危）
- [ ] D2. 在 `.github/workflows/ci.yml` 的 frontend job 中插入 audit 步骤（原规划新建独立 frontend.yml，实际合并到统一 ci.yml）
- [ ] D3. workflow 文件 lint 合规

### 批次 E：后端依赖预留（REQ-5a/5b/5c）

- [ ] E1. request.ts 加 `registerErrorReporter` 导出（空实现+注释）
- [ ] E2. Login.vue 加 B0005 错误分支（预留+注释）
- [ ] E3. auth.ts 加 `refresh_token` 空字段；request.ts 401 处理加刷新预留注释
- [ ] E4. vitest 全量通过（确认无半成品逻辑影响现有行为）

## 全量交叉审核

- [ ] X1. `code-review-report-plugin` 生成交互报告，无严重/高危
- [ ] X2. security-best-practices 报告复验，4 个待办问题中 REQ-1/2/3/4 已关闭，REQ-5 预留项已标注
- [ ] X3. vitest 39 文件 349 用例全绿（与基线一致）
- [ ] X4. OpenSpec 归档：`openspec archive frontend-security-hardening`
