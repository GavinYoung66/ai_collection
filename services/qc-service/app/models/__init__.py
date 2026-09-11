"""qc-service ORM：五表对齐 step4/01。对外主键名 qc_record_id = qc_records.id。"""
from app.models.fk_targets import register_fk_targets

register_fk_targets()
from app.models.qc import QcAlert, QcRecord, QcReview, QcRule, QcViolation

__all__ = ["QcAlert", "QcRecord", "QcReview", "QcRule", "QcViolation"]
