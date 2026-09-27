# arm64 Spike Report — rocketmq-local-bus

**日期**: 2026-09-27  
**主机**: macOS arm64 + Docker Desktop  
**镜像**: `apache/rocketmq:5.3.2`  
**结论**: **go** → 后续走 `common/eventbus/rocketmq.py`

---

## 结论摘要

| 项 | 结果 |
|----|------|
| 1.1 arm64 镜像可用性 | PASS（`--platform linux/arm64` 拉取成功；本地 `Arch=arm64`） |
| 1.2 NameServer+Broker+Proxy 最小 compose | PASS（三组件 Up；`nc -z localhost 19876` 成功） |
| 1.3 宿主机 Python SDK 环回 | PASS（`LOOPBACK_PASS`） |
| **go / no-go** | **go** |

---

## 1.1 Manifest / 拉取证据

证据目录：`openspec/changes/rocketmq-local-bus/spike-evidence/`

### Multi-arch 平台清单（含 linux/arm64）

本机 `docker manifest inspect` 访问 `registry-1.docker.io` 超时（见 `spike-evidence/manifest-inspect.txt`）。改用 Docker Hub Registry API 等价清单：

```bash
# GET https://hub.docker.com/v2/repositories/apache/rocketmq/tags/5.3.2
# 落盘: spike-evidence/hub-tag-5.3.2-platforms.json
```

摘要（index digest 与本地拉取一致）：

| os/arch | digest（平台镜像） |
|---------|-------------------|
| linux/amd64 | `sha256:06d64e9af63f…` |
| **linux/arm64** | `sha256:d923ab25ddbb…` |

Index / RepoDigest：`sha256:4a80ff5cd4b0a3d3ef6dc338ce2028a85bd995648cb3aa5c3e753746d2dbc587`

### 本地拉取成功（tasks 验收 OR 条件）

```bash
docker pull --platform linux/arm64 apache/rocketmq:5.3.2
# Digest: sha256:4a80ff5cd4b0a3d3ef6dc338ce2028a85bd995648cb3aa5c3e753746d2dbc587
# Status: Downloaded newer image for apache/rocketmq:5.3.2

docker image inspect apache/rocketmq:5.3.2 --format 'Arch={{.Architecture}} Os={{.Os}} Digest={{index .RepoDigests 0}}'
# Arch=arm64 Os=linux Digest=apache/rocketmq@sha256:4a80ff5cd4b0a3d3ef6dc338ce2028a85bd995648cb3aa5c3e753746d2dbc587
# 落盘: spike-evidence/image-inspect-arch.json
```

---

## 1.2 Compose 启动证据

Spike 专用文件（未改主 `docker-compose.yml`，留给 §3）：

- `openspec/changes/rocketmq-local-bus/docker-compose.spike.yml`
- `openspec/changes/rocketmq-local-bus/spike-broker.conf`（`autoCreateTopicEnable=true`，`brokerIP1=rocketmq-broker`）
- `openspec/changes/rocketmq-local-bus/spike-rmq-proxy.json`（`useEndpointPortFromRequest: true`）

```bash
cd openspec/changes/rocketmq-local-bus
docker compose -f docker-compose.spike.yml up -d
docker compose -f docker-compose.spike.yml ps
```

输出摘要（含 healthcheck；三组件均为 `healthy`）：

```
NAME               IMAGE                   STATUS                    PORTS
rmqbroker-spike    apache/rocketmq:5.3.2   Up ... (healthy)          10909/10911/10912
rmqnamesrv-spike   apache/rocketmq:5.3.2   Up ... (healthy)          0.0.0.0:19876->9876/tcp
rmqproxy-spike     apache/rocketmq:5.3.2   Up ... (healthy)          0.0.0.0:18080->8080/tcp, 0.0.0.0:18081->8081/tcp
```

```bash
nc -zv localhost 19876   # Connection ... succeeded!
nc -zv localhost 18081   # Connection ... succeeded!
```

备注：Proxy 仍保留 `restart: on-failure`；compose 增加 TCP healthcheck，且 Proxy `depends_on` Broker/NameServer `condition: service_healthy`，降低系统 Topic 冷启动竞态。tasks 1.2 措辞已与验收对齐为 running/healthy。

---

## 1.3 Python SDK 环回证据

依赖：`rocketmq-python-client==5.1.1`（宿主机 pip 安装，**未**改 `requirements.txt`，留给 §3.3）

脚本：`openspec/changes/rocketmq-local-bus/spike_loopback.py`

前置（broker 容器内）：

```bash
docker exec rmqbroker-spike sh -c \
  'cd /home/rocketmq/rocketmq-5.3.2/bin && \
   sh mqadmin updateTopic -n rocketmq-namesrv:9876 -t spike_loopback -c DefaultCluster && \
   sh mqadmin updateSubGroup -n rocketmq-namesrv:9876 -g gid_spike_loopback -c DefaultCluster'
```

命令与输出：

```bash
OTEL_SDK_DISABLED=true python3 spike_loopback.py
# SEND_OK message_id=014646813919FA28080ACA767D00000000 body=b'spike-loopback-a1f17c695599'
# RECV_OK message_id=014646813919FA28080ACA767D00000000 tag=spike.test body=b'spike-loopback-a1f17c695599'
# LOOPBACK_PASS
```

### §3 必带清单（已写入 design Decision 8；正式 compose/SDK 不得省略）

| # | 约束 | Spike 落点 | §3 动作 |
|---|------|------------|---------|
| 1 | Proxy `useEndpointPortFromRequest: true` | `spike-rmq-proxy.json` + compose 挂载 `-pc` | 主 compose 同等配置 |
| 2 | Proxy 冷启动可重试；Broker 允许自动建 Topic | Proxy `restart: on-failure`；`spike-broker.conf` `autoCreateTopicEnable=true` | 主 compose 同等策略 |
| 3 | Producer 勿预声明 `topics=` 再 `startup()` | `spike_loopback.py` | `rocketmq.py` 默认同路径 |
| 4 | Consumer 订阅用 `FilterExpression(...)` | `spike_loopback.py` | `rocketmq.py` subscribe 同路径 |

---

## 1.4 go/no-go

| 分支 | 条件 | 本轮 |
|------|------|------|
| **go** → `rocketmq.py` | arm64 可跑 + 宿主机 SDK 环回成功 | **采用** |
| no-go → `redis_streams.py` | 镜像不可用或 Proxy/SDK 无法环回 | 不采用 |

**最终结论：go。** 后续实现按 design Decision 6 走 RocketMQ 适配器路径。
