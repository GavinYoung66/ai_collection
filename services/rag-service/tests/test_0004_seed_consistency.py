"""迁移 0004 seed 一致性校验（独立可运行）。

断言 DB sensitive_words status='active' count=50，且与 data/sensitive_words.dict
集合完全一致（word 集合相等，顺序无关）。

依赖 conftest.py 的 `async_db_session` fixture（受 RAG_DB_TESTS 控制，
未设置时 fixture 内部 skip，回归不红）。
"""
from pathlib import Path

import pytest

DICT_FILE = Path(__file__).resolve().parents[1] / "data" / "sensitive_words.dict"


def _load_dict_words() -> set[str]:
    """读取 .dict 文件全部词（去空行去首尾空白）。"""
    text = DICT_FILE.read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip()}


@pytest.mark.asyncio
async def test_seed_consistency(async_db_session):
    """DB 50 词与 .dict 文件集合完全一致。"""
    from sqlalchemy import select
    from app.models.orm import SensitiveWord

    result = await async_db_session.execute(
        select(SensitiveWord.word).where(
            SensitiveWord.status == "active", SensitiveWord.is_delete == "0"
        )
    )
    db_words = {row[0] for row in result.all()}

    dict_words = _load_dict_words()

    assert len(db_words) >= 50, f"DB active words count < 50: {len(db_words)}"
    assert db_words == dict_words, (
        f"DB words != dict words. "
        f"Only in DB: {db_words - dict_words}. "
        f"Only in dict: {dict_words - db_words}"
    )
