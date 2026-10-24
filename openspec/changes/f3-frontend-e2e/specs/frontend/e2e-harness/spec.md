# Spec Delta

## Purpose

前端交互层确定性回归基础设施：以真实浏览器验证静态门禁与 jsdom 单测不可见的壳层交互时序缺陷（导航展开语义、角色路由守卫、qc 关键按钮态），零后端依赖、CI 可重复执行。

## ADDED Requirements

### Requirement: E2E 必须使用真实浏览器且不依赖后端

E2E MUST 运行于真实浏览器（Playwright chromium），网络层 MUST 以 `/api/**` 全量 mock 隔离，MUST NOT 依赖 qc-service、网关或 RocketMQ 等后端运行时；E2E MUST NOT 断言数据字段正确性（该职责由契约单测与接口矩阵承担）。

#### Scenario: 无后端时用例可独立运行
- **WHEN** 后端服务全部未启动，仅启动 vite dev server 并执行 `npm run test:e2e`
- **THEN** 全部用例正常执行完成，无网络错误导致的失败

#### Scenario: 数据请求被 mock 拦截
- **WHEN** 用例进入 `/qc/tasks` 并触发 `demo/pending` 请求
- **THEN** 响应来自 mock fixture，页面不发出任何指向真实后端的请求

### Requirement: 导航展开语义必须被回归锁定

E2E MUST 包含「跨一级菜单切换后回到带二级分组的父级菜单」用例，断言 MUST 在稳定等待后二级菜单仍处于展开态；等待时长 MUST 大于任一懒加载 chunk 的常规加载时间（≥1.5s），仅断言点击瞬间展开 MUST NOT 视为通过。

#### Scenario: 跨菜单切换后二级菜单保持展开
- **WHEN** 用户点击「LLM 用量」，随后点击「合规质检」父级标题，并等待 1.5 秒
- **THEN** 「合规质检」的二级菜单仍处于展开态，且 URL 已落到该分组第一个子路由

#### Scenario: 重复点击折叠、分组互斥
- **WHEN** 用户连续两次点击同一父级标题，或点击另一父级标题
- **THEN** 前者折叠、后者互斥展开（同时仅一个分组展开）

### Requirement: 角色路由必须在浏览器级复验

E2E MUST 以 admin / supervisor / qc_reviewer 三角色分别验证 §11.1 收窄后的菜单可见性，并验证越权直输 URL 落 `/403` 且不发起对应数据请求。

#### Scenario: 越权直输 URL 被拦截
- **WHEN** `qc_reviewer` 直接访问 `/qc/rules` 或 `/qc/alerts`，或 `admin` 直接访问 `/qc/reviews`
- **THEN** 浏览器落到 `/403`，目标页面不渲染、不发起对应端点请求，侧边栏亦不显示该菜单项

### Requirement: qc 关键按钮态必须被浏览器级覆盖

E2E MUST 覆盖 qc 主链路的三类按钮态：任务台行内开检在途防连点、详情页人审按钮按角色置灰/可用、零扣分记录不渲染提交按钮。

#### Scenario: 开检在途防连点
- **WHEN** 用户在任务台对同一行连续点击「开始质检」两次且首请求未返回
- **THEN** 该行按钮呈禁用态，mock 计数为 1

#### Scenario: admin 人审按钮置灰
- **WHEN** `admin` 打开质检详情页
- **THEN** 人审操作组呈禁用态并展示角色说明，且不出现可提交的复核按钮

#### Scenario: 零扣分不渲染提交
- **WHEN** 打开扣分为 0 的质检记录详情
- **THEN** 页面不渲染任何复核提交按钮

### Requirement: E2E 门禁必须经过自测证明有效

CI MUST 包含 blocking 的 `frontend-e2e` job（禁止 `continue-on-error`），且验收过程 MUST 故意改坏一条断言以观察红态，随后还原；MUST NOT 在未证明可失败的情况下宣称该门禁有效。

#### Scenario: 改坏断言后门禁变红
- **WHEN** 临时修改导航回归用例的断言使其与真实行为不符
- **THEN** 本地 `npm run test:e2e` 与 CI job 均以非 0 退出，还原后恢复全绿
