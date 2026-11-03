"""T36（tasks 8.4）生产部署基线不变量测试。

T36 属部署编排项，不改变运行时对外契约，因此以编排文件的结构断言钉住整改结果：
- `docker-compose.prod.yml` 存在且可解析，覆盖真实运行栈（不含 dev 专属 mock/空壳/观测栈）；
- RocketMQ 主从（SYNC_MASTER + SLAVE），消息落独立命名卷而非容器可写层；
- 端口收敛：仅 Agent 入口（mcp-gateway）与业务 HTTP 入口（complaint-service）发布宿主机端口；
- 生产形态：无 `--reload`、无源码 bind mount、`ENV=prod`、不内置 dev 默认口令。

docker daemon 不在测试环境内，故只做静态结构校验（`docker compose config` 另行人工演练）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PROD = "docker-compose.prod.yml"
_BASE = "docker-compose.yml"
_DEV = "docker-compose.dev.yml"

# 允许对外发布宿主机端口的服务白名单（其余一律仅容器内网）
_ALLOWED_PUBLISHED = {
    "mcp-gateway": "9009:9000",
    "complaint-service": "8006:8000",
}

# dev 专属、不应进入生产基线的服务
_DEV_ONLY = {
    "rag-dict-mock",  # 7 词冒烟 mock
    "prometheus",  # 本地观测栈
    "grafana",
}

_APP_SERVICES = (
    "data-platform",
    "rag-service",
    "qc-service",
    "mcp-gateway",
    "complaint-service",
    "complaint-worker",
)

_INFRA_SERVICES = (
    "postgres",
    "redis",
    "etcd",
    "minio",
    "milvus",
    "elasticsearch",
    "ocr-service",
)


def _load(name: str) -> dict:
    with open(_ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _prod() -> dict:
    return _load(_PROD)


def _published_ports(spec: dict) -> list[str]:
    """把 compose 的 ports 条目归一为 "host:container" 字符串（raw 编排为字符串形式）。"""
    out: list[str] = []
    for item in spec.get("ports") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):  # `docker compose config` 归一化后的形态
            out.append(f"{item['published']}:{item['target']}")
        else:  # pragma: no cover - 防御性
            out.append(str(item))
    return out


def _volume_sources(spec: dict) -> list[str]:
    sources: list[str] = []
    for vol in spec.get("volumes") or []:
        if isinstance(vol, str):
            sources.append(vol.split(":", 1)[0])
        elif isinstance(vol, dict):
            sources.append(vol.get("source", ""))
    return sources


def _is_named_volume(source: str) -> bool:
    return bool(source) and not source.startswith(".") and not source.startswith("/")


def test_prod_compose_exists_and_parses() -> None:
    """生产基线文件存在、可解析，且包含全部真实服务。"""
    assert (_ROOT / _PROD).is_file()
    cfg = _prod()
    services = cfg["services"]
    for name in (*_INFRA_SERVICES, *_APP_SERVICES):
        assert name in services, f"prod compose 缺少真实服务 {name}"


def test_prod_covers_real_dev_stack() -> None:
    """基线与真实一致：dev 的真实服务都在 prod 中，dev 专属 mock/观测栈不在。"""
    base = _load(_BASE)["services"]
    dev = _load(_DEV)["services"]
    # dev 中用 profiles 标注的 health 空壳（call/case/review）不进入生产基线（T24）
    shell_only = {name for name, spec in dev.items() if spec.get("profiles")}
    expected = (set(base) | set(dev)) - _DEV_ONLY - shell_only
    actual = set(_prod()["services"])
    assert expected <= actual, f"prod 缺少 dev 真实服务：{sorted(expected - actual)}"
    assert actual.isdisjoint(_DEV_ONLY | shell_only)
    for name in shell_only:
        assert name not in actual


def test_only_entry_services_publish_host_ports() -> None:
    """端口收敛：仅入口服务发布宿主机端口，基础设施与其余服务只 expose。"""
    cfg = _prod()
    published: dict[str, list[str]] = {}
    for name, spec in cfg["services"].items():
        ports = _published_ports(spec)
        if ports:
            published[name] = ports

    assert published == {k: [v] for k, v in _ALLOWED_PUBLISHED.items()}

    # 基础设施一律不得发布端口（原问题：PG/Redis/Milvus/ES/MinIO/etcd/RocketMQ 裸暴露）
    for name in (*_INFRA_SERVICES, *(n for n in cfg["services"] if "rocketmq" in n)):
        assert not _published_ports(cfg["services"][name]), name

    # 非入口的 HTTP 服务只走容器内网（complaint-worker 无 HTTP 端点，无需 expose）
    for name in _APP_SERVICES:
        if name in _ALLOWED_PUBLISHED or name == "complaint-worker":
            continue
        assert cfg["services"][name].get("expose"), name


def test_rocketmq_master_slave_topology() -> None:
    """RocketMQ 主从：同 brokerName、主 brokerId=0、从 brokerId=1，从节点依赖主节点。"""
    cfg = _prod()
    services = cfg["services"]
    assert "rocketmq-broker" in services
    assert "rocketmq-broker-slave" in services

    master_conf = (_ROOT / "deploy/rocketmq/broker-prod.conf").read_text(encoding="utf-8")
    slave_conf = (_ROOT / "deploy/rocketmq/broker-slave.conf").read_text(encoding="utf-8")

    assert "brokerId = 0" in master_conf
    assert "brokerRole = SYNC_MASTER" in master_conf
    assert "brokerName = broker-a" in master_conf

    assert "brokerId = 1" in slave_conf
    assert "brokerRole = SLAVE" in slave_conf
    assert "brokerName = broker-a" in slave_conf

    # 从节点在主节点 healthy 后启动，且经 namesrv 注册同一集群
    slave_depends = services["rocketmq-broker-slave"]["depends_on"]
    assert slave_depends["rocketmq-broker"]["condition"] == "service_healthy"
    for broker in ("rocketmq-broker", "rocketmq-broker-slave"):
        assert services[broker]["environment"]["NAMESRV_ADDR"] == "rocketmq-namesrv:9876"


def test_rocketmq_store_on_distinct_named_volumes() -> None:
    """消息数据落命名卷：主/从各一独立卷，且卷在顶层 volumes 声明。"""
    cfg = _prod()
    declared = set(cfg.get("volumes") or {})
    master = cfg["services"]["rocketmq-broker"]
    slave = cfg["services"]["rocketmq-broker-slave"]

    master_stores = [s for s in _volume_sources(master) if _is_named_volume(s)]
    slave_stores = [s for s in _volume_sources(slave) if _is_named_volume(s)]
    assert len(master_stores) == 1 and master_stores[0] in declared
    assert len(slave_stores) == 1 and slave_stores[0] in declared
    assert master_stores[0] != slave_stores[0], "主从不得共用同一存储卷"

    # 卷挂载点必须与 broker 配置的 storePathRootDir 一致
    for broker, stores in ((master, master_stores), (slave, slave_stores)):
        mounts = [v for v in broker["volumes"] if isinstance(v, str) and v.startswith(stores[0])]
        assert mounts and mounts[0].split(":")[1] == "/home/rocketmq/store"

    for conf in ("deploy/rocketmq/broker-prod.conf", "deploy/rocketmq/broker-slave.conf"):
        text = (_ROOT / conf).read_text(encoding="utf-8")
        assert "storePathRootDir = /home/rocketmq/store" in text, conf


def test_rocketmq_store_permissions_prepared_before_start() -> None:
    """命名卷首次为 root 所有；broker 启动前必须完成 uid 5000 chown（否则启动失败）。"""
    cfg = _prod()
    init = cfg["services"]["rocketmq-store-init"]
    assert init["user"] == "root"
    assert "chown" in " ".join(init["command"])
    for broker in ("rocketmq-broker", "rocketmq-broker-slave"):
        depends = cfg["services"][broker]["depends_on"]
        assert depends["rocketmq-store-init"]["condition"] == "service_completed_successfully"


def test_prod_compose_has_no_dev_hot_reload_or_source_mounts() -> None:
    """生产形态：无 uvicorn --reload，无 common/services 源码 bind mount。"""
    cfg = _prod()
    for name, spec in cfg["services"].items():
        # 注释里可以提到 --reload，运行时命令不得带热重载
        command = spec.get("command") or spec.get("entrypoint") or []
        assert "--reload" not in " ".join(str(part) for part in command), name

        for source in _volume_sources(spec):
            assert source not in {"./common", "./services"}, f"{name} 挂载了源码目录 {source}"
            assert not source.startswith("./services/") or "/config/" in source, name


def test_prod_services_run_in_production_mode() -> None:
    """ENV=prod 强制认证开启与占位密钥 fail-fast；complaint 走真实 MCP/LLM。"""
    cfg = _prod()
    for name in _APP_SERVICES:
        assert cfg["services"][name]["environment"]["ENV"] == "prod", name

    for name in ("complaint-service", "complaint-worker"):
        env = cfg["services"][name]["environment"]
        assert env["USE_MOCK_MCP"] == "false"
        assert env["USE_MOCK_LLM"] == "false"
        assert env["MCP_GATEWAY_MODE"] == "standalone"


def test_prod_compose_has_no_builtin_dev_credentials() -> None:
    """生产口令/密钥一律由 .env（sops）注入；文件不得内置 dev 默认值。"""
    text = (_ROOT / _PROD).read_text(encoding="utf-8")
    assert "minioadmin" not in text
    assert "${PG_PASSWORD:?must set PG_PASSWORD" in text
    assert "${MINIO_ACCESS_KEY:?must set MINIO_ACCESS_KEY" in text
    assert "${MINIO_SECRET_KEY:?must set MINIO_SECRET_KEY" in text


def test_complaint_worker_is_separate_process() -> None:
    """T25 的进程边界在生产基线同样成立：worker 独立容器、无对外端口。"""
    worker = _prod()["services"]["complaint-worker"]
    assert worker["command"] == ["python", "-m", "app.worker"]
    assert not _published_ports(worker)
