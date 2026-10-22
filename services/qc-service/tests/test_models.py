"""QC-2.1：五表 ORM / CHECK 与 step4/01 对齐。"""
from sqlalchemy import inspect

from app.models.qc import QcAlert, QcRecord, QcReview, QcRule, QcViolation


def _cols(model) -> set[str]:
    return {c.key for c in inspect(model).columns}


def _check_sql(model) -> str:
    chunks = []
    for c in model.__table__.constraints:
        sql = getattr(c, "sqltext", None)
        if sql is not None:
            chunks.append(str(sql))
    return " ".join(chunks)


class TestQcRecordOrm:
    def test_table_and_external_pk_name(self):
        assert QcRecord.__tablename__ == "qc_records"
        row = QcRecord()
        row.id = None
        assert hasattr(QcRecord, "qc_record_id")

    def test_frozen_column_names(self):
        cols = _cols(QcRecord)
        assert "compliance_score" in cols
        assert "deduction_total" in cols
        assert "risk_level" in cols
        assert "qc_result" in cols
        assert "grade" in cols
        assert "qc_status" in cols
        assert "score" not in cols
        assert "risk" not in cols
        assert "quality_id" not in cols
        assert "skipped_rules" not in cols

    def test_session_id_unique_fk(self):
        col = inspect(QcRecord).columns["session_id"]
        assert col.unique is True
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "call_records.session_id"

    def test_external_fk_columns_resolve(self):
        expected = {"session_id": "call_records", "case_id": "cases", "agent_id": "users"}
        for col, table in expected.items():
            fks = list(QcRecord.__table__.c[col].foreign_keys)
            assert fks[0].column.table.name == table

    def test_qc_result_and_status_checks(self):
        sql = _check_sql(QcRecord)
        assert "compliant" in sql and "violation" in sql and "pending_review" in sql
        assert "DONE" in sql and "PENDING_REVIEW" in sql and "REVIEWED" in sql and "PARTIAL" in sql
        assert "WAIT_TEXT" not in sql
        assert "FAILED" not in sql
        assert "RUNNING" not in sql
        assert "EXCELLENT" in sql and "PASS" in sql and "WARNING" in sql and "SERIOUS" in sql

    def test_deduction_total_check(self):
        sql = _check_sql(QcRecord)
        assert "deduction_total >= 0" in sql

    def test_assert_db_status_rejects_wait_text(self):
        try:
            QcRecord.assert_db_status("WAIT_TEXT")
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
        assert QcRecord.assert_db_status("PARTIAL") == "PARTIAL"


class TestChildTables:
    def test_violations_speaker_and_severity(self):
        sql = _check_sql(QcViolation)
        assert "agent" in sql and "user" in sql
        assert "high" in sql and "medium" in sql and "low" in sql
        assert QcViolation.__tablename__ == "qc_violations"
        cols = _cols(QcViolation)
        assert "qc_record_id" in cols
        assert "source" in cols
        assert "word" in cols
        assert "kb_snippet" in cols
        assert "points" in cols
        assert "policy_clause" in cols
        assert "policy_text" in cols
        sql = _check_sql(QcViolation)
        assert "ac" in sql and "rule" in sql and "llm" in sql
        assert "points >= 0" in sql

    def test_alerts_level_high_only(self):
        sql = _check_sql(QcAlert)
        assert "high" in sql
        assert "medium" not in sql
        assert QcAlert.__tablename__ == "qc_alerts"

    def test_reviews_actions(self):
        sql = _check_sql(QcReview)
        assert "CONFIRM" in sql and "DISMISS" in sql and "CORRECT" in sql

    def test_reviews_revised_deduction_check(self):
        cols = _cols(QcReview)
        assert "revised_deduction" in cols
        sql = _check_sql(QcReview)
        assert "revised_deduction IS NULL OR revised_deduction >= 0" in sql

    def test_rules_status_and_version(self):
        cols = _cols(QcRule)
        assert "version" in cols
        assert "rules" in cols
        assert "penalty" in cols
        assert "same_word_window_ms" in cols
        assert "grade_thresholds" in cols
        assert "deduction_policy" in cols
        sql = _check_sql(QcRule)
        assert "draft" in sql and "active" in sql and "archived" in sql
        assert inspect(QcRule).columns["version"].unique is True
