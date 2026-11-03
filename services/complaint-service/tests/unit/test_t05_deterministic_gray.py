"""T05 回归：灰度分片必须跨进程/重启稳定（不得依赖受 PYTHONHASHSEED 影响的 hash()）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.service.mode_service import ModeService, gray_bucket

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _SERVICE_ROOT.parent.parent


def _service():
    service = ModeService()
    from app.service.mode_service import KEY_ACTIVE, KEY_GRAY

    service._redis.set(KEY_ACTIVE, "multi_agent")
    service._redis.set(KEY_GRAY, "50")
    return service


def test_bucket_is_pinned_to_crc32():
    """固定标识的分片值写死，算法漂移（例如又被换成 hash()）会直接失败。"""
    assert gray_bucket("session-2026-0001") == 74
    assert gray_bucket("session-2026-0002") == 0
    assert gray_bucket("session-2026-0003") == 26


@pytest.mark.parametrize("seed", ["0", "1", "42", "random"])
def test_bucket_stable_across_pythonhashseed(seed):
    """子进程用不同 PYTHONHASHSEED 计算同一标识，结果必须一致。"""
    script = (
        "from app.service.mode_service import gray_bucket;"
        "print(gray_bucket('session-2026-0001'))"
    )
    env = {
        **os.environ,
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": f"{_SERVICE_ROOT}:{_REPO_ROOT}",
    }
    out = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) == 74


def test_gray_routing_matches_bucket_at_boundaries():
    """percent 与分片的比较关系决定灰度命中，不能出现分片边界漂移。"""
    service = _service()

    service._redis.set("csmp:mode:gray_percent", "75")
    assert service.should_use_single_agent("session-2026-0001") is True

    service._redis.set("csmp:mode:gray_percent", "74")
    assert service.should_use_single_agent("session-2026-0001") is False
