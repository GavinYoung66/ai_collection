"""种子数据：初始用户（admin/supervisor/agent/qc_reviewer/ai_trainer）。

口令不得硬编码：按角色从环境变量读取（SEED_*_PASSWORD），缺失即非零退出；
生产环境（ENV=prod/production）禁止执行本脚本。
"""
import asyncio
import sys

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import _PLACEHOLDER_SECRETS, settings
from common.db.models.user import User
from common.db.session import async_session_factory

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# 按角色注入口令的环境变量（由本地 .env / 密钥系统提供，禁止写入仓库）
ROLE_PASSWORD_ENV = {
    "admin": "SEED_ADMIN_PASSWORD",
    "supervisor": "SEED_SUPERVISOR_PASSWORD",
    "agent": "SEED_AGENT_PASSWORD",
    "qc_reviewer": "SEED_QC_REVIEWER_PASSWORD",
    "ai_trainer": "SEED_AI_TRAINER_PASSWORD",
}

USERS = [
    {"username": "admin", "real_name": "系统管理员", "role": "admin"},
    {"username": "supervisor01", "real_name": "质检主管A", "role": "supervisor"},
    {"username": "supervisor02", "real_name": "质检主管B", "role": "supervisor"},
    {"username": "agent01", "real_name": "催收坐席01", "role": "agent"},
    {"username": "agent02", "real_name": "催收坐席02", "role": "agent"},
    {"username": "agent03", "real_name": "催收坐席03", "role": "agent"},
    {"username": "agent04", "real_name": "催收坐席04", "role": "agent"},
    {"username": "agent05", "real_name": "催收坐席05", "role": "agent"},
    {"username": "qc_reviewer01", "real_name": "质检复核员01", "role": "qc_reviewer"},
    {"username": "trainer01", "real_name": "AI 训练师01", "role": "ai_trainer"},
]


def _refuse_in_production() -> None:
    env = (settings.ENV or "").strip().lower()
    if env in {"prod", "production"}:
        print(f"拒绝执行：生产环境（ENV={settings.ENV}）禁止运行种子脚本", file=sys.stderr)
        raise SystemExit(2)


def _load_role_passwords() -> dict[str, str]:
    """从环境变量读取各角色口令；缺失或占位值（如 <change_me>）均视为缺失，非零退出。

    占位值必须拦截：.env.example 模板中的 SEED_*_PASSWORD=<change_me> 若被原样
    复制，会把字面占位串哈希成真实口令落库，重新引入已知默认凭据。
    """
    missing: list[str] = []
    values: dict[str, str] = {}
    for role, var in ROLE_PASSWORD_ENV.items():
        value = str(getattr(settings, var, "") or "").strip()
        if value.lower() in _PLACEHOLDER_SECRETS:
            missing.append(var)
        else:
            values[role] = value
    if missing:
        print(
            "缺少种子口令环境变量：" + ", ".join(missing) + "（必须由环境变量/密钥系统注入）",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return values


async def seed():
    _refuse_in_production()
    passwords = _load_role_passwords()
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
                password_hash=pwd_context.hash(passwords[user_data["role"]]),
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
