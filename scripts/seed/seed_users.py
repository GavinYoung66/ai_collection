"""种子数据：初始用户（admin/supervisor/agent/qc_reviewer/ai_trainer）。"""
import asyncio

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.models.user import User
from common.db.session import async_session_factory

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

USERS = [
    {"username": "admin", "password": "Admin@2026", "real_name": "系统管理员", "role": "admin"},
    {"username": "supervisor01", "password": "Super@2026", "real_name": "质检主管A", "role": "supervisor"},
    {"username": "supervisor02", "password": "Super@2026", "real_name": "质检主管B", "role": "supervisor"},
    {"username": "agent01", "password": "Agent@2026", "real_name": "催收坐席01", "role": "agent"},
    {"username": "agent02", "password": "Agent@2026", "real_name": "催收坐席02", "role": "agent"},
    {"username": "agent03", "password": "Agent@2026", "real_name": "催收坐席03", "role": "agent"},
    {"username": "agent04", "password": "Agent@2026", "real_name": "催收坐席04", "role": "agent"},
    {"username": "agent05", "password": "Agent@2026", "real_name": "催收坐席05", "role": "agent"},
    {"username": "qc_reviewer01", "password": "Qc@2026", "real_name": "质检复核员01", "role": "qc_reviewer"},
    {"username": "trainer01", "password": "Train@2026", "real_name": "AI 训练师01", "role": "ai_trainer"},
]


async def seed():
    async with async_session_factory() as session:  # type: AsyncSession
        for user_data in USERS:
            existing = await session.execute(
                select(User).where(User.username == user_data["username"])
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  skip: {user_data['username']} already exists")
                continue

            user = User(
                username=user_data["username"],
                password_hash=pwd_context.hash(user_data["password"]),
                real_name=user_data["real_name"],
                role=user_data["role"],
                status="active",
                agent_skills=[] if user_data["role"] == "agent" else None,
            )
            session.add(user)
            print(f"  created: {user_data['username']} ({user_data['role']})")

        await session.commit()
        print(f"Done: {len(USERS)} users processed.")


if __name__ == "__main__":
    asyncio.run(seed())
