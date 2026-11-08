"""T119：rag 同名文档写入竞态原子性（部分唯一索引 + IntegrityError 转 B0001 + 冲突自愈）。

spec：rag-service/document-ingest「文档写入竞态原子性」三场景——
1. 并发同名上传只保留一条（仅一条进入 active/processing，另一条明确冲突，无双 processing）；
2. 已存在同名 active 文档 → 既有重复上传错误语义（B0001），不创建新记录；
3. 冲突后可自愈（查询可见唯一一条有效记录，无遗留僵尸 processing 行）。

现场裁决（以实际代码为准，留痕）：
- 部分唯一索引形态：kb_documents(title) 单列唯一，WHERE status IN ('active','processing')
  AND is_delete='0'（迁移 0019，仓库根 migrations/versions/ 全服务共享 Alembic 链——
  报告所引 services/rag-service/migrations/ 不存在，为路径漂移）。与 doc_repo
  check-then-act 两查询（仅按 title 判重、不分 category）及迁移 0002 放宽后的
  status CHECK 同口径；spec 未定义「不同类目同名不冲突」，跨类目同名同样冲突。
- 报告所引 documents.py:85-94 实际在 :87-97（行号漂移 +2，以实际代码为准）。

测试形态：内存 KB 模型（committed/pending 双区 + commit 转正 / rollback 撤销）模拟
事务与部分唯一索引语义（fake 无真实挂起点处用 sleep(0) 制造交错）；HTTP 层用例走
TestClient（对齐 test_t92_documents_access 形态），机制闭环用例直接调用端点函数。
"""
from __future__ import annotations

import asyncio
import inspect
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db
from app.api.documents import upload_document
from app.main import app
from app.repositories.doc_repo import DocRepo
from app.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.security.rbac import create_token

# 现场事实：documents router 无 prefix，main.py 挂载带 /api/v1/kb/documents
_UPLOAD_PATH = "/api/v1/kb/documents"

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"


def _pg_orig(pgcode: str | None, msg: str) -> Exception:
    """构造带 pgcode 的 IntegrityError.orig（对齐 complaint T54 测试形态）。"""
    orig = Exception(msg)
    orig.pgcode = pgcode
    return orig


def _dup_integrity_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO kb_documents (title, category, status, content) VALUES (...)",
        None,
        _pg_orig(
            "23505",
            'duplicate key value violates unique constraint "uq_kb_documents_active_title"',
        ),
    )


class MemoryKB:
    """kb_documents 内存模型：committed（已提交）+ pending（在途未提交）。"""

    def __init__(self):
        self.committed: dict[str, SimpleNamespace] = {}
        self.pending: dict[str, SimpleNamespace] = {}


class FakeSession:
    """内存事务模型：create 注册 pending，commit 转正、rollback 撤销（无僵尸行）。

    fail_commit 注入模拟「flush 成功后、commit 阶段命中唯一索引」的并发时序。
    """

    def __init__(self, kb: MemoryKB, fail_commit: IntegrityError | None = None):
        self.kb = kb
        self._mine: list[str] = []
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        if self.fail_commit is not None:
            raise self.fail_commit
        for pid in self._mine:
            self.kb.committed[pid] = self.kb.pending.pop(pid)
        self._mine.clear()

    async def rollback(self):
        self.rollbacks += 1
        for pid in self._mine:
            self.kb.pending.pop(pid, None)
        self._mine.clear()


def _install_memory_repo(monkeypatch, kb: MemoryKB) -> list[str]:
    """把 DocRepo 上传路径方法替换为内存实现（模拟部分唯一索引语义）。

    - check-then-act 两查询只读 committed 行（READ COMMITTED 可见性近似）；
    - create 在 committed+pending 中存在同 title 的 active/processing 行时抛
      IntegrityError(23505)——pending 占用把「并发窗口在 commit 阶段收敛」提前到
      flush 阶段（真实 PG 中在途未提交行对其他事务不可见、commit 时冲突），
      冲突结果语义一致；
    - create 注册 pending 后 sleep(0) 让出，为 gather 用例制造真实交错窗口。
    返回 create_calls 供断言「不创建新记录」。
    """
    create_calls: list[str] = []

    def _blocking_row(title):
        for doc in list(kb.committed.values()) + list(kb.pending.values()):
            if doc.title == title and doc.status in ("active", "processing"):
                return doc
        return None

    def _visible_row(title):
        for doc in kb.committed.values():
            if doc.title == title and doc.status in ("active", "processing"):
                return doc
        return None

    async def find_processing(self, title):
        row = _visible_row(title)
        return row if row is not None and row.status == "processing" else None

    async def find_active(self, title):
        row = _visible_row(title)
        return row if row is not None and row.status == "active" else None

    async def create(self, title, category, content, created_by=None, source=None):
        create_calls.append(title)
        if _blocking_row(title) is not None:
            raise _dup_integrity_error()
        doc = SimpleNamespace(
            id=uuid.uuid4(), title=title, category=category, status="processing"
        )
        kb.pending[str(doc.id)] = doc
        self.db._mine.append(str(doc.id))
        await asyncio.sleep(0)  # 制造交错窗口（check 双通过 → create 撞索引的时序可达）
        return doc.id

    async def update_ingest_stage(self, doc_id, stage, progress=None):
        return None

    monkeypatch.setattr("app.api.documents.DocRepo.find_processing_by_title", find_processing)
    monkeypatch.setattr("app.api.documents.DocRepo.find_active_by_title", find_active)
    monkeypatch.setattr("app.api.documents.DocRepo.create", create)
    monkeypatch.setattr("app.api.documents.DocRepo.update_ingest_stage", update_ingest_stage)
    return create_calls


def _install_staging(monkeypatch, tmp_path: Path, staged: dict) -> None:
    async def fake_staging(file, doc_id):
        dest = tmp_path / f"{doc_id}{Path(file.filename or 'x.txt').suffix}"
        dest.write_bytes(b"staged-content")
        staged[str(doc_id)] = dest
        return dest

    monkeypatch.setattr("app.api.documents.save_upload_to_staging", fake_staging)


def _install_schedule(monkeypatch, scheduled: list) -> None:
    def fake_schedule(doc_id, file_path, filename, category, summary=None):
        scheduled.append(str(doc_id))

    monkeypatch.setattr("app.api.documents.schedule_background_ingest", fake_schedule)


@pytest.fixture()
def upload_env(monkeypatch, tmp_path):
    """TestClient 上传环境：内存 repo + staging/schedule 替身 + 限流放行。"""
    kb = MemoryKB()
    session = FakeSession(kb)
    staged: dict[str, Path] = {}
    scheduled: list[str] = []
    create_calls = _install_memory_repo(monkeypatch, kb)
    _install_staging(monkeypatch, tmp_path, staged)
    _install_schedule(monkeypatch, scheduled)
    monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
    # 冲突后 staging 清理使用 settings.KB_INGEST_STAGING_DIR——指向 tmp_path 以便断言
    monkeypatch.setattr(settings, "KB_INGEST_STAGING_DIR", str(tmp_path))

    async def _allow(key, limit, window, context=None):
        return True

    monkeypatch.setattr("app.services.rate_limit._rate_limit_allow", _allow)

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    yield SimpleNamespace(
        kb=kb, session=session, staged=staged, scheduled=scheduled, create_calls=create_calls
    )
    app.dependency_overrides.clear()


@pytest.fixture()
def client(upload_env):
    yield TestClient(app)


def _upload(filename: str = "dup.txt") -> UploadFile:
    return UploadFile(file=io.BytesIO(b"content"), filename=filename)


def _post(client: TestClient, title: str = "dup.txt", category: str = "regulation"):
    return client.post(
        _UPLOAD_PATH,
        files={"file": (title, b"content", "text/plain")},
        data={"category": category, "summary": ""},
        headers={
            "Authorization": f"Bearer {create_token({'sub': 'ops', 'role': 'supervisor'})}"
        },
    )


class TestExistingDuplicateSemantics:
    """既有 check-then-act 语义回归（spec 场景 1 顺序形态 + 场景 2）。"""

    def test_processing_duplicate_rejected_b0001_no_create(self, client, upload_env):
        upload_env.kb.committed["p1"] = SimpleNamespace(
            id=uuid.uuid4(), title="dup.txt", category="regulation", status="processing"
        )
        resp = _post(client)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "入库中" in body["message"]
        assert upload_env.create_calls == []  # 不创建新记录

    def test_active_duplicate_rejected_b0001_existing_semantics(self, client, upload_env):
        """spec 场景 2：同名 active 存在 → 既有重复上传错误语义，不创建新记录。"""
        upload_env.kb.committed["a1"] = SimpleNamespace(
            id=uuid.uuid4(), title="dup.txt", category="regulation", status="active"
        )
        resp = _post(client)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "已在知识库中" in body["message"]
        assert upload_env.create_calls == []


class TestNonBlockingStates:
    """部分索引 WHERE 口径：failed / 已删除行不占用同名名额。"""

    def test_failed_status_does_not_block_reupload(self, client, upload_env):
        upload_env.kb.committed["f1"] = SimpleNamespace(
            id=uuid.uuid4(), title="dup.txt", category="regulation", status="failed"
        )
        resp = _post(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["status"] == "processing"
        assert upload_env.create_calls == ["dup.txt"]

    def test_archived_row_does_not_block_reupload(self, client, upload_env):
        upload_env.kb.committed["d1"] = SimpleNamespace(
            id=uuid.uuid4(), title="dup.txt", category="regulation", status="archived"
        )
        resp = _post(client)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "processing"
        assert upload_env.create_calls == ["dup.txt"]


class TestConcurrentUniqueIndexGuard:
    """T119 新语义：唯一索引冲突 IntegrityError → rollback（无僵尸行）→ 自愈 → B0001。"""

    def test_flush_conflict_converted_to_b0001(self, client, upload_env, monkeypatch):
        """check 双通过 + create(flush) 命中唯一索引 → 明确 B0001（非 B0007），
        rollback 撤销本事务行，后台入库不调度，staging 未落盘。"""
        async def _none(self, title):
            return None

        monkeypatch.setattr("app.api.documents.DocRepo.find_processing_by_title", _none)
        monkeypatch.setattr("app.api.documents.DocRepo.find_active_by_title", _none)

        async def _dup(self, title, category, content, created_by=None, source=None):
            upload_env.create_calls.append(title)
            raise _dup_integrity_error()

        monkeypatch.setattr("app.api.documents.DocRepo.create", _dup)

        resp = _post(client)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "同名" in body["message"]
        assert upload_env.session.rollbacks == 1
        assert upload_env.scheduled == []
        assert upload_env.staged == {}
        assert not any(
            d.title == "dup.txt" and d.status in ("active", "processing")
            for d in upload_env.kb.committed.values()
        )

    def test_commit_conflict_self_heals_reports_existing_processing(
        self, client, upload_env, monkeypatch
    ):
        """spec 场景 1+3（commit 阶段冲突）：rollback 后自愈查询既有 processing 行，
        B0001 消息携带既有 doc_id 供调用方收敛；staging 文件清理、无僵尸行。"""
        peer_id = uuid.uuid4()
        peer = SimpleNamespace(
            id=peer_id, title="dup.txt", category="regulation", status="processing"
        )
        calls = {"n": 0}

        async def _find_processing(self, title):
            calls["n"] += 1
            # 首查（check-then-act）并发窗口内不可见；rollback 后自愈查询可见
            return None if calls["n"] == 1 else peer

        async def _find_active(self, title):
            return None

        monkeypatch.setattr("app.api.documents.DocRepo.find_processing_by_title", _find_processing)
        monkeypatch.setattr("app.api.documents.DocRepo.find_active_by_title", _find_active)
        upload_env.session.fail_commit = _dup_integrity_error()

        resp = _post(client)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "入库中" in body["message"]
        assert str(peer_id) in body["message"]
        assert upload_env.session.rollbacks == 1
        assert upload_env.scheduled == []
        assert upload_env.kb.pending == {}  # 无僵尸行
        # staging 文件（若已落盘）被 best-effort 清理
        assert upload_env.staged and all(not p.exists() for p in upload_env.staged.values())

    def test_conflict_self_heal_reports_existing_active_doc(
        self, client, upload_env, monkeypatch
    ):
        """自愈查询命中 active 行 → 既有重复上传语义 + 既有 doc_id。"""
        peer_id = uuid.uuid4()
        peer = SimpleNamespace(
            id=peer_id, title="dup.txt", category="regulation", status="active"
        )
        calls = {"n": 0}

        async def _find_processing(self, title):
            return None

        async def _find_active(self, title):
            calls["n"] += 1
            return None if calls["n"] == 1 else peer

        monkeypatch.setattr("app.api.documents.DocRepo.find_processing_by_title", _find_processing)
        monkeypatch.setattr("app.api.documents.DocRepo.find_active_by_title", _find_active)
        upload_env.session.fail_commit = _dup_integrity_error()

        resp = _post(client)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "已在知识库中" in body["message"]
        assert str(peer_id) in body["message"]

    def test_non_unique_integrity_error_stays_b0007(self, client, upload_env, monkeypatch):
        """非唯一冲突的 IntegrityError（如 FK 23503）保持 B0007 真实失败语义。"""
        async def _none(self, title):
            return None

        monkeypatch.setattr("app.api.documents.DocRepo.find_processing_by_title", _none)
        monkeypatch.setattr("app.api.documents.DocRepo.find_active_by_title", _none)

        async def _fk(self, title, category, content, created_by=None, source=None):
            upload_env.create_calls.append(title)
            raise IntegrityError(
                "INSERT ...", None,
                _pg_orig("23503", "insert or update on table violates foreign key constraint"),
            )

        monkeypatch.setattr("app.api.documents.DocRepo.create", _fk)

        resp = _post(client)
        assert resp.status_code == 400
        assert resp.json()["code"] == "B0007"
        assert upload_env.session.rollbacks == 1
        assert upload_env.scheduled == []

    def test_same_title_different_category_still_conflicts(self, client, upload_env):
        """跨类目同名按 title 冲突（现场裁决留痕）：spec 未定义「不同类目同名不冲突」，
        check-then-act（find_* 仅按 title 判重）与单列 title 部分唯一索引同口径。"""
        upload_env.kb.committed["a1"] = SimpleNamespace(
            id=uuid.uuid4(), title="dup.txt", category="regulation", status="active"
        )
        resp = _post(client, category="script")
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0001"
        assert "已在知识库中" in body["message"]
        assert upload_env.create_calls == []


class TestGatherConvergence:
    """机制闭环（spec 场景 1+3）：内存模型下并发同名上传收敛为一条有效记录。"""

    @pytest.mark.asyncio
    async def test_gather_same_title_only_one_active_processing(self, monkeypatch, tmp_path):
        kb = MemoryKB()
        staged: dict[str, Path] = {}
        _install_memory_repo(monkeypatch, kb)
        _install_staging(monkeypatch, tmp_path, staged)
        _install_schedule(monkeypatch, [])
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
        monkeypatch.setattr(settings, "KB_INGEST_STAGING_DIR", str(tmp_path))

        async def one():
            return await upload_document(
                file=_upload("dup.txt"),
                category="regulation",
                summary="",
                auth=None,
                rate=None,
                db=FakeSession(kb),
            )

        results = await asyncio.gather(one(), one(), return_exceptions=True)
        successes = [r for r in results if isinstance(r, dict)]
        conflicts = [r for r in results if isinstance(r, BizException)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0]["data"]["status"] == "processing"
        assert conflicts[0].code == ErrorCode.B0001
        assert "同名" in conflicts[0].message
        # 场景 1：仅一条进入 active/processing（无双 processing 记录）
        processing_rows = [d for d in kb.committed.values() if d.status == "processing"]
        assert len(processing_rows) == 1
        # 场景 3：无遗留僵尸 processing 行（pending 全部收敛）
        assert kb.pending == {}
        # 仅成功方落 staging 并调度后台入库
        assert len(staged) == 1


class TestMigrationFormGuard:
    """迁移形态守护：索引存在性、部分条件与 check-then-act 判重口径锚定。"""

    def test_migration_0019_partial_unique_index_form(self):
        path = _MIGRATIONS_DIR / "0019_kb_documents_active_title_unique.py"
        assert path.exists(), "迁移 0019 必须存在（T119 唯一 DB 迁移项）"
        src = path.read_text(encoding="utf-8")
        assert 'revision: str = "0019"' in src
        assert 'down_revision: Union[str, None] = "0018"' in src
        assert "CREATE UNIQUE INDEX" in src
        assert "uq_kb_documents_active_title" in src
        assert "ON kb_documents(title)" in src
        # 部分条件：仅约束 active/processing 且未删除（failed/已删除不占用）
        assert "WHERE status IN ('active','processing') AND is_delete = '0'" in src
        assert "DROP INDEX IF EXISTS" in src

    def test_index_scope_matches_repo_check_queries(self):
        """索引 WHERE 的 status 集合与 doc_repo check-then-act 两查询锚定——
        防止单侧口径漂移造成「check 放行但索引拦截」或反向漏网。"""
        processing_src = inspect.getsource(DocRepo.find_processing_by_title)
        active_src = inspect.getsource(DocRepo.find_active_by_title)
        assert 'KBDocument.title == title' in processing_src
        assert 'KBDocument.status == "processing"' in processing_src
        assert 'KBDocument.title == title' in active_src
        assert 'KBDocument.status == "active"' in active_src
        mig = (
            _MIGRATIONS_DIR / "0019_kb_documents_active_title_unique.py"
        ).read_text(encoding="utf-8")
        assert "'active','processing'" in mig