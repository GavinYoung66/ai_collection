"""演示破例：qc 写入 cases / call_records / dialog_turns。生产 replay 路径不得调用本仓库。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.seeds.demo_scenarios import DemoTurn

DEMO_REVIEWER_USERNAME = "qc_demo_reviewer"
DEMO_REVIEWER_NAME = "质检演示复核"
_PASSWORD_PLACEHOLDER = "$2b$12$qc_demo_reviewer_not_for_login_xxxxxxxxxxxxxxxx"


class DemoSeedRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_reviewer(self) -> UUID:
        found = await self.session.execute(
            text("SELECT id FROM users WHERE username = :username AND is_delete = '0' LIMIT 1"),
            {"username": DEMO_REVIEWER_USERNAME},
        )
        existing = found.scalar_one_or_none()
        if existing is not None:
            return existing if isinstance(existing, UUID) else UUID(str(existing))
        user_id = uuid4()
        await self.session.execute(
            text(
                """
                INSERT INTO users (id, username, password_hash, real_name, role, status)
                VALUES (:id, :username, :password_hash, :real_name, 'qc_reviewer', 'active')
                """
            ),
            {
                "id": user_id,
                "username": DEMO_REVIEWER_USERNAME,
                "password_hash": _PASSWORD_PLACEHOLDER,
                "real_name": DEMO_REVIEWER_NAME,
            },
        )
        return user_id

    async def insert_ended_call(
        self,
        *,
        case_id: UUID,
        call_id: UUID,
        session_id: str,
        case_number: str,
        customer_name: str,
        phone_encrypted: str,
        phone_hash: str,
        overdue_amount: str | Decimal,
        overdue_days: int,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: int,
        turns: list[DemoTurn],
    ) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO cases (
                    id, case_number, customer_name, phone_encrypted, phone_hash,
                    overdue_amount, overdue_days, account_stage, case_status
                ) VALUES (
                    :id, :case_number, :customer_name, :phone_encrypted, :phone_hash,
                    :overdue_amount, :overdue_days, 'M1', 'processing'
                )
                """
            ),
            {
                "id": case_id,
                "case_number": case_number,
                "customer_name": customer_name,
                "phone_encrypted": phone_encrypted,
                "phone_hash": phone_hash,
                "overdue_amount": str(overdue_amount),
                "overdue_days": overdue_days,
            },
        )
        await self.session.execute(
            text(
                """
                INSERT INTO call_records (
                    id, session_id, case_id, call_type, call_status,
                    start_time, end_time, duration_seconds, result
                ) VALUES (
                    :id, :session_id, :case_id, 'ai', 'completed',
                    :start_time, :end_time, :duration_seconds, NULL
                )
                """
            ),
            {
                "id": call_id,
                "session_id": session_id,
                "case_id": case_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration_seconds": duration_seconds,
            },
        )
        for turn in turns:
            speaker = turn["speaker"]
            if speaker not in {"ai", "user"}:
                raise ValueError(f"dialog_turns.speaker must be ai|user, got {speaker!r}")
            await self.session.execute(
                text(
                    """
                    INSERT INTO dialog_turns (id, session_id, turn_number, speaker, text)
                    VALUES (:id, :session_id, :turn_number, :speaker, :text)
                    """
                ),
                {
                    "id": uuid4(),
                    "session_id": session_id,
                    "turn_number": turn["turn_number"],
                    "speaker": speaker,
                    "text": turn["text"],
                },
            )
