"""Prompt 版本/A-B 流量管理。

- select_version(prompt_id, session_id) -> str：基于哈希确定性选版本
- configure_ab(prompt_id, distribution)：设置版本→百分比
- rollback(prompt_id, version)：一键回滚为该版本 100%（进程内立即生效）

边界：配置保存在本进程内存，不跨进程/实例共享。rollback 只影响执行它的进程；
多副本部署时须对每个副本分别执行（或后续改由配置中心下发），否则其余副将继续
按旧分布分流。
"""
from __future__ import annotations

import hashlib
from threading import Lock

import structlog

logger = structlog.get_logger()

# 进程内 A/B 配置：{prompt_id: {version: percentage}}
_ab_config: dict[str, dict[str, int]] = {}
_lock = Lock()


def configure_ab(prompt_id: str, distribution: dict[str, int]) -> None:
    """设置 A/B 流量分配。

    Args:
        prompt_id: Prompt 标识
        distribution: {version: percentage}，百分比总和应为 100
    """
    total = sum(distribution.values())
    if total != 100:
        raise ValueError(f"distribution percentages must sum to 100, got {total}")
    with _lock:
        _ab_config[prompt_id] = dict(distribution)


def select_version(prompt_id: str, session_id: str) -> str:
    """根据 session_id 哈希选择版本（同会话稳定）。

    Args:
        prompt_id: Prompt 标识
        session_id: 会话 ID

    Returns:
        版本号

    Raises:
        KeyError: prompt_id 未配置 A/B
    """
    with _lock:
        dist = _ab_config.get(prompt_id)
    if not dist:
        raise KeyError(f"No A/B config for prompt_id={prompt_id}")

    versions = list(dist.keys())
    weights = list(dist.values())

    # 哈希取模落在哪个版本区间
    h = int(hashlib.md5(f"{session_id}:{prompt_id}".encode()).hexdigest(), 16)
    bucket = h % 100

    cumulative = 0
    for ver, w in zip(versions, weights):
        cumulative += w
        if bucket < cumulative:
            return ver
    # 兜底返回最后一个
    return versions[-1]


def rollback(prompt_id: str, version: str) -> None:
    """一键回滚：将 prompt_id 流量 100% 切到指定版本（仅当前进程生效）。"""
    with _lock:
        _ab_config[prompt_id] = {version: 100}
    logger.warning(
        "prompt_ab_rollback",
        prompt_id=prompt_id,
        version=version,
        scope="process-local",
    )


def get_ab_config(prompt_id: str) -> dict[str, int] | None:
    """查看当前 A/B 配置（测试用）。"""
    with _lock:
        return dict(_ab_config.get(prompt_id, {})) if prompt_id in _ab_config else None
