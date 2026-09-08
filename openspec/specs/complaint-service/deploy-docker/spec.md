## Purpose

定义投诉服务生产 Dockerfile 规范：对齐 M0 统一模板，镜像可构建、健康检查可达，作为批次11 部署基线。

## Purpose

定义投诉服务生产 Dockerfile 规范：对齐 M0 统一模板，镜像可构建、健康检查可达，作为批次11 部署基线。

## Requirements

### Requirement: Dockerfile 对齐 M0 模板

系统 SHALL 提供 `services/complaint-service/Dockerfile`，基于 `python:3.11.15-slim`，按以下顺序构建：安装系统依赖（curl、gcc、libc6-dev）、`COPY requirements.lock` 并 pip 安装、`COPY common/`、`COPY services/complaint-service/app/` 到 `/app/app/`，`ENV PYTHONPATH=/app`，`CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`。已有文件时 SHALL 增量对齐，不得无故重写无关层。Dockerfile 内 `HEALTHCHECK` 为可选（compose 侧 healthcheck 已覆盖即可）。

#### Scenario: 镜像可构建

- **WHEN** 执行 `docker build -f services/complaint-service/Dockerfile .`
- **THEN** 构建成功，镜像内含 `/app/app/main.py` 与 `/app/common/`

#### Scenario: 容器内端口为 8000

- **WHEN** 容器启动
- **THEN** uvicorn 监听容器内 8000 端口（compose 侧映射为 8006）

### Requirement: 健康检查依赖

系统 SHALL 在镜像内安装 `curl`，使 compose 的 healthcheck 可通过 `curl -f http://localhost:8000/health` 探活。

#### Scenario: 健康检查可执行

- **WHEN** 容器内执行 `which curl`
- **THEN** 返回 `/usr/bin/curl`（或等价路径），healthcheck 命令不缺依赖
