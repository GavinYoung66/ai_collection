"""种子数据一键初始化：按顺序执行所有 seed 脚本。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.seed.seed_users import seed as seed_users
from scripts.seed.seed_sensitive_words import seed as seed_sensitive_words
from scripts.seed.seed_qc_rules import seed as seed_qc_rules
from scripts.seed.seed_strategies import seed as seed_strategies


async def main():
    print("=== 1/4 Seeding users ===")
    await seed_users()
    print("\n=== 2/4 Seeding sensitive words ===")
    await seed_sensitive_words()
    print("\n=== 3/4 Seeding QC rules ===")
    await seed_qc_rules()
    print("\n=== 4/4 Seeding strategies + skill tags ===")
    await seed_strategies()
    print("\n=== All seed data loaded ===")


if __name__ == "__main__":
    asyncio.run(main())
