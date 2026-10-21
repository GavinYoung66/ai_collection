"""跨进程图锁（Step 7）单元测试。

重点验证三态语义与 fail-open：Redis 不可用时必须退化为「仅进程内守卫」，
而不是像 `idempotency.acquire` 那样 fail-closed 导致研判全面停摆。
"""
from __future__ import annotations

import pytest

from app.models.state import TaskStatus
from app.service import graph_lock as gl
from common.resilience._redis import configure_redis


class _FakeRedis:
    """可切换可用性的内存 Redis 替身（含 nx/ex 语义）。

    刻意**不实现 eval**：用于验证「无 eval 时退化为 GET 比对再删」这条路径
    仍然做 token 校验，不会盲目删除他人的锁。
    """

    def __init__(self, available: bool = True) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, object] = {}
        self.available = available

    async def set(self, key, value, nx=False, ex=None):  # noqa: ARG002
        if not self.available:
            raise OSError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        if not self.available:
            raise OSError("redis down")
        return self.store.get(key)

    async def expire(self, key, ttl):  # noqa: ARG002
        if not self.available:
            raise OSError("redis down")
        self.ttl[key] = ttl
        return key in self.store

    async def delete(self, *keys):
        if not self.available:
            raise OSError("redis down")
        return sum(1 for k in keys if self.store.pop(k, None) is not None)


class _FakeRedisWithEval(_FakeRedis):
    """实现 eval 的替身，用于覆盖 Lua CAS 这条主路径（释放/续期）。"""

    async def eval(self, script, numkeys, key, token, *args):  # noqa: ARG002
        if not self.available:
            raise OSError("redis down")
        if self.store.get(key) != token:
            return 0
        if "expire" in script:
            self.ttl[key] = args[0] if args else None
            return 1
        return self.store.pop(key, None) is not None


async def test_acquire_then_second_is_held():
    """同一工单第二次获取返回 HELD（他人持有），而非再次成功。"""
    configure_redis(_FakeRedis())
    assert await gl.acquire_graph_lock("c-lock-1", 60, token=gl.new_token()) == gl.LOCK_ACQUIRED
    assert await gl.acquire_graph_lock("c-lock-1", 60) == gl.LOCK_HELD


async def test_redis_down_is_unavailable_not_held():
    """Redis 不可用 → UNAVAILABLE（可继续），**不能**误报 HELD（会停摆）。"""
    configure_redis(_FakeRedis(available=False))
    assert await gl.acquire_graph_lock("c-lock-2", 60) == gl.LOCK_UNAVAILABLE


async def test_release_frees_lock():
    configure_redis(_FakeRedis())
    token = gl.new_token()
    await gl.acquire_graph_lock("c-lock-3", 60, token=token)
    await gl.release_graph_lock("c-lock-3", token)
    assert await gl.acquire_graph_lock("c-lock-3", 60) == gl.LOCK_ACQUIRED


@pytest.mark.parametrize("fake_cls", [_FakeRedis, _FakeRedisWithEval],
                         ids=["no-eval", "with-eval"])
async def test_release_with_wrong_token_does_not_steal(fake_cls):
    """经典竞态：A 的锁过期被 B 拿到后，A 用旧 token 释放不得删掉 B 的锁。

    两条释放路径都要满足：Lua CAS（有 eval）与 GET 比对降级（无 eval）。
    """
    configure_redis(fake_cls())
    await gl.acquire_graph_lock("c-lock-4", 60, token="token-A")
    # 模拟 A 的锁过期后 B 拿到（直接覆盖）
    await gl.release_graph_lock("c-lock-4", "token-A")
    await gl.acquire_graph_lock("c-lock-4", 60, token="token-B")
    # A 用旧 token 再释放一次：不应删掉 B 的锁
    await gl.release_graph_lock("c-lock-4", "token-A")
    assert await gl.acquire_graph_lock("c-lock-4", 60) == gl.LOCK_HELD


async def test_resume_lock_uses_separate_key():
    """graph_lock 与 resume_lock 必须互不干扰（judge 与 resume 会并发）。"""
    configure_redis(_FakeRedis())
    await gl.acquire_graph_lock("c-lock-5", 60, token=gl.new_token())
    assert await gl.acquire_resume_lock("c-lock-5", 60) == gl.LOCK_ACQUIRED


@pytest.mark.parametrize("fake_cls", [_FakeRedis, _FakeRedisWithEval],
                         ids=["no-eval", "with-eval"])
async def test_extend_only_for_own_token(fake_cls):
    """续期只能延长自己持有的锁，且确实刷新了 TTL。

    锁需覆盖「轮询等断点 + 执行 resume」两段；不续期会在执行段中途过期被抢。
    """
    from app.utils.redis_keys import resume_lock_key

    fake = fake_cls()
    configure_redis(fake)
    await gl.acquire_resume_lock("c-ext-1", 60, token="token-A")

    assert await gl.extend_resume_lock("c-ext-1", 1800, "token-A") is True
    assert fake.ttl.get(resume_lock_key("c-ext-1")) == 1800, "TTL 未被刷新"
    # 他人 token 不得延长（也不得改 TTL）
    assert await gl.extend_resume_lock("c-ext-1", 9999, "token-B") is False
    assert fake.ttl.get(resume_lock_key("c-ext-1")) == 1800


@pytest.mark.parametrize("fake_cls", [_FakeRedis, _FakeRedisWithEval],
                         ids=["no-eval", "with-eval"])
async def test_extend_fails_open_when_redis_down(fake_cls):
    configure_redis(fake_cls(available=False))
    assert await gl.extend_resume_lock("c-ext-2", 1800, "tok") is False


@pytest.mark.parametrize("fake_cls", [_FakeRedis, _FakeRedisWithEval],
                         ids=["no-eval", "with-eval"])
async def test_release_does_not_raise_when_redis_down(fake_cls):
    """释放失败不得抛异常：finally 里调用，抛了会掩盖真正的业务异常。"""
    configure_redis(fake_cls(available=False))
    await gl.release_graph_lock("c-rel-1", "tok")
    await gl.release_resume_lock("c-rel-1", "tok")


def test_as_str_decodes_bytes():
    """redis 客户端 decode_responses 配置不同，返回值可能是 bytes。"""
    assert gl._as_str(b"token") == "token"
    assert gl._as_str("token") == "token"


@pytest.mark.parametrize("fake_cls", [_FakeRedis, _FakeRedisWithEval],
                         ids=["no-eval", "with-eval"])
async def test_release_frees_lock_for_next_holder(fake_cls):
    """释放后他人可以获取（否则同单研判会被永久挡住）。"""
    configure_redis(fake_cls())
    token = gl.new_token()
    await gl.acquire_graph_lock("c-rel-2", 60, token=token)
    await gl.release_graph_lock("c-rel-2", token)
    assert await gl.acquire_graph_lock("c-rel-2", 60) == gl.LOCK_ACQUIRED


async def test_judge_async_skips_when_graph_lock_held(monkeypatch):
    """跨进程锁被他人持有时，judge_async 直接返回当前态，不跑图。"""
    from app.service import complaint_service as cs_module
    from app.service.complaint_service import complaint_service

    cid = "c-lock-held-001"
    rec = complaint_service.create("投诉", case_id=cid)

    async def _held(complaint_id, ttl, token=None):  # noqa: ARG001
        return gl.LOCK_HELD

    monkeypatch.setattr(cs_module, "acquire_graph_lock", _held)

    out = await complaint_service.judge_async(cid, run_graph=True)
    assert out["complaint_id"] == rec["complaint_id"]
    assert out["task_status"] == TaskStatus.pending.value


async def test_judge_async_proceeds_when_lock_unavailable(monkeypatch):
    """Redis 不可用（UNAVAILABLE）时必须继续跑图，服务保持可用。"""
    from app.service import complaint_service as cs_module
    from app.service.complaint_service import complaint_service

    cid = "c-lock-unavail-001"
    complaint_service.create("投诉", case_id=cid)

    async def _unavailable(complaint_id, ttl, token=None):  # noqa: ARG001
        return gl.LOCK_UNAVAILABLE

    ran: list[str] = []

    async def _spy_judge(rec, complaint_id):
        ran.append(complaint_id)
        return rec

    monkeypatch.setattr(cs_module, "acquire_graph_lock", _unavailable)
    monkeypatch.setattr(complaint_service, "_judge_with_graph", _spy_judge)

    await complaint_service.judge_async(cid, run_graph=True)
    assert ran == [cid], "Redis 不可用时不应阻断研判"


async def test_judge_async_releases_lock_on_success(monkeypatch):
    """正常跑完图后必须释放跨进程锁，否则后续同单研判被永久挡住。"""
    from app.service import complaint_service as cs_module
    from app.service.complaint_service import complaint_service

    cid = "c-lock-release-001"
    complaint_service.create("投诉", case_id=cid)

    released: list[str] = []

    async def _acquired(complaint_id, ttl, token=None):  # noqa: ARG001
        return gl.LOCK_ACQUIRED

    async def _spy_release(complaint_id, token):  # noqa: ARG001
        released.append(complaint_id)

    async def _spy_judge(rec, complaint_id):
        return rec

    monkeypatch.setattr(cs_module, "acquire_graph_lock", _acquired)
    monkeypatch.setattr(cs_module, "release_graph_lock", _spy_release)
    monkeypatch.setattr(complaint_service, "_judge_with_graph", _spy_judge)

    await complaint_service.judge_async(cid, run_graph=True)
    assert released == [cid]


async def test_second_replica_skips_while_first_holds_lock(monkeypatch):
    """模拟两副本并发研判同一工单：只有一次真正进图，第二次被跨进程锁挡下。

    关键点：第二次调用前**清掉本进程的 _graph_inflight**，模拟"另一个副本
    没有这份进程内状态"——这正是单靠进程内守卫防不住、必须上 Redis 锁的场景。
    若无此锁，两路 astream 会写同一 thread_id，checkpoint 分叉导致 resume 失效。
    """
    from app.config import settings
    from app.service import complaint_service as cs_module
    from app.service.complaint_service import complaint_service

    cid = "c-two-replicas-001"
    complaint_service.create("投诉", case_id=cid)

    ran: list[str] = []

    async def _slow_judge(rec, complaint_id):
        """第一个副本：跑图期间触发第二个副本的并发请求。"""
        ran.append("replica-1")
        # 模拟另一副本：它看不到本进程的 _graph_inflight
        cs_module._graph_inflight.discard(complaint_id)
        assert settings.USE_MOCK_MCP is True  # 保证走内存路径，不碰 PG

        out = await complaint_service.judge_async(complaint_id, run_graph=True)
        assert out["task_status"] == TaskStatus.pending.value, "第二副本应被锁挡下"
        return rec

    monkeypatch.setattr(complaint_service, "_judge_with_graph", _slow_judge)

    await complaint_service.judge_async(cid, run_graph=True)
    assert ran == ["replica-1"], "第二个副本不应真正进图"
