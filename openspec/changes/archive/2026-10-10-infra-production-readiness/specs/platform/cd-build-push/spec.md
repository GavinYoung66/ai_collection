## Purpose

为 8 个服务提供标准化的镜像构建与分发流水线（CD Level 1）：代码打 tag 后自动通过门禁、构建镜像并推送至阿里云 ACR，产出可被任何环境拉取的版本化制品，但不触碰服务器侧部署。

## ADDED Requirements

### Requirement: tag 触发与门禁前置

系统 SHALL 新增 `.github/workflows/cd.yml`，仅在推送匹配 `v*` 的 git tag 时触发（普通分支推送 MUST NOT 触发构建推送）。镜像构建与推送 MUST 在同一 tag 对应提交通过与 CI 等价的测试门禁之后才允许执行；门禁未过则 CD 失败、不产出镜像。

#### Scenario: 打 tag 自动触发流水线

- **WHEN** 推送一个形如 `v0.1.0` 的 tag
- **THEN** CD workflow 自动运行；普通 commit/分支推送不会触发该 workflow

#### Scenario: 门禁不过不产镜像

- **WHEN** tag 对应提交未通过 CI 等价测试门禁
- **THEN** CD 在构建前失败，ACR 中不出现该 tag 的新镜像

### Requirement: 八服务镜像构建

CD SHALL 构建 `services/` 下全部 8 个服务的镜像：data-platform、rag-service、qc-service、mcp-gateway、complaint-service、call-service、case-service、review-service，复用各服务目录中既有 Dockerfile（以仓库根为构建上下文）。若清点时发现某服务缺少 Dockerfile，本能力须补齐后方可交付。`services/` 之外的镜像（gradio、infra、mock 等）MUST NOT 由本流水线构建。

#### Scenario: 八服务均完成镜像构建

- **WHEN** CD 流水线执行构建阶段
- **THEN** 8 个服务各自成功构建出镜像，每个服务对应一个明确的 Dockerfile 路径；无服务被遗漏

#### Scenario: 不构建范围外镜像

- **WHEN** CD 流水线执行
- **THEN** 不构建也不推送 services/ 目录之外的任何镜像

### Requirement: 推送 ACR 与双 tag

每个服务镜像 SHALL 推送到阿里云 ACR `crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection/{service}`（新个人版实例独立域名），并同时打两个 tag：触发流水线的 git tag（如 `v0.1.0`）与 `latest`。推送完成后，8 个服务的版本化镜像 MUST 均可从 ACR 独立拉取成功。

#### Scenario: 版本化镜像可拉取

- **WHEN** 推送 `v0.1.0` 且 CD 成功后执行 `docker pull crpi-7ki4lc0lyjmk9zm7.cn-hangzhou.personal.cr.aliyuncs.com/ai-collection/{service}:v0.1.0`
- **THEN** 对全部 8 个服务拉取均成功

#### Scenario: latest 同步更新

- **WHEN** CD 推送完成
- **THEN** 每个服务仓库下 `{git_tag}` 与 `latest` 两个 tag 指向同一次构建的同一镜像

### Requirement: 仓库凭证管理

ACR 登录凭证 SHALL 通过 GitHub secrets（`ACR_USERNAME`、`ACR_PASSWORD`）注入流水线，MUST NOT 硬编码在 workflow 文件或仓库任何位置，MUST NOT 出现在流水线日志中。

#### Scenario: 凭证来自 secrets

- **WHEN** CD 执行容器仓库登录
- **THEN** 使用 GitHub secrets 注入的凭证完成登录，workflow 文件与构建日志中均不存在明文凭证

### Requirement: 仅构建分发不做部署

本能力边界止于镜像推送成功：MUST NOT 包含任何服务器登录、远程部署、容器重启、滚动/蓝绿/灰度发布动作；环境升级由人工拉取对应 tag 镜像另行执行。

#### Scenario: 流水线结束后无服务器侧变更

- **WHEN** CD 全流程成功结束
- **THEN** 唯一产物是 ACR 中的 8 组镜像；任何运行中环境的服务版本与配置均不发生变化
