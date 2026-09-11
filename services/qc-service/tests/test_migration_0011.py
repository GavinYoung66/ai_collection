"""QC-2.2：Alembic 0011 顺序与禁止项。"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_MIG = _REPO / "migrations" / "versions" / "0011_qc_service_v2_split_violations.py"
_INIT = _REPO / "migrations" / "versions" / "0001_init_tables.py"


def _upgrade_src() -> str:
    text = _MIG.read_text(encoding="utf-8")
    return text.split("def downgrade")[0]


class TestMigration0011:
    def test_file_exists(self):
        assert _MIG.is_file()

    def test_create_tables_before_backfill(self):
        src = _upgrade_src()
        create_pos = src.index("CREATE TABLE qc_violations")
        backfill_pos = src.index("INSERT INTO qc_violations")
        assert create_pos < backfill_pos

    def test_must_not_drop_jsonb_violations_column(self):
        src = _upgrade_src().lower()
        assert re.search(r"drop\s+column\s+(if\s+exists\s+)?violations\b", src) is None
        assert "drop column qc_records.violations" not in src

    def test_must_not_drop_session_id_fk(self):
        """0011 增量加 UNIQUE，不得 DROP session_id 列/外键；FK 来自 0001 而非 COMMENT。"""
        src = _upgrade_src()
        lowered = src.lower()
        assert re.search(
            r"drop\s+constraint\s+(if\s+exists\s+)?\w*qc_records_session_id\w*fkey",
            lowered,
        ) is None
        assert re.search(r"drop\s+constraint[^\n]*session_id[^\n]*fkey", lowered) is None
        assert re.search(r"drop\s+column\s+(if\s+exists\s+)?session_id\b", lowered) is None
        compact = re.sub(r"\s+", " ", src)
        assert "uq_qc_records_session_id" in src
        assert "UNIQUE (session_id)" in compact
        init_src = _INIT.read_text(encoding="utf-8")
        assert "session_id VARCHAR(100) NOT NULL REFERENCES call_records(session_id)" in init_src

    def test_alembic_upgrade_sql_compiles(self):
        import subprocess
        import sys

        repo = _REPO
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "0011",
                "--sql",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": f"{repo / 'services' / 'qc-service'}:{repo}"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "CREATE TABLE qc_violations" in proc.stdout
        assert "INSERT INTO qc_violations" in proc.stdout
        assert "drop column violations" not in proc.stdout.lower()
        assert "v1.0" in proc.stdout
        assert "起诉" in proc.stdout
        src = _upgrade_src()
        assert "CREATE TABLE qc_rules" in src
        assert "idx_qc_rules_active" in src
        assert "INSERT INTO qc_rules" in src
        assert "v1.0" in src
