"""QC demo-web 1.2：Alembic 0012 为 qc_violations 增加 source/word/kb_snippet。"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MIG = _REPO / "migrations" / "versions" / "0012_qc_violations_source_word_kb_snippet.py"


def _upgrade_src() -> str:
    text = _MIG.read_text(encoding="utf-8")
    return text.split("def downgrade")[0]


class TestMigration0012:
    def test_file_exists_and_revises_0011(self):
        assert _MIG.is_file()
        src = _MIG.read_text(encoding="utf-8")
        assert 'revision: str = "0012"' in src
        assert 'down_revision: Union[str, None] = "0011"' in src

    def test_adds_nullable_hit_columns_not_jsonb(self):
        src = _upgrade_src()
        upgrade_fn = src[src.index("def upgrade") :]
        lowered = upgrade_fn.lower()
        assert "add column if not exists source" in lowered
        assert "add column if not exists word" in lowered
        assert "add column if not exists kb_snippet" in lowered
        assert "update qc_records" not in lowered
        assert "violations jsonb" not in lowered
        assert "drop column" not in lowered

    def test_alembic_upgrade_sql_compiles(self):
        import os
        import subprocess
        import sys

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "0012",
                "--sql",
            ],
            cwd=_REPO,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": f"{_REPO / 'services' / 'qc-service'}:{_REPO}"},
        )
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout.lower()
        assert "qc_violations" in out
        assert "source" in out
        assert "word" in out
        assert "kb_snippet" in out
        assert "drop column violations" not in out
