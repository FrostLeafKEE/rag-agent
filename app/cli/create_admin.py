"""管理 CLI：创建/提升管理员账号（替代数据库直改）。

用法：
  uv run python -m app.cli.create_admin --username admin --password 'xxx' [--department 研发部]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.api.security import hash_password
from app.db import get_session
from app.models import User


async def _run(username: str, password: str, department: str) -> None:
    async for session in get_session():
        user = await session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                department=department,
                role="admin",
            )
            session.add(user)
            print(f"已创建管理员：{username}")
        else:
            user.role = "admin"
            if department:
                user.department = department
            print(f"已将 {username} 提升为管理员")
        await session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="创建/提升管理员账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--department", default="")
    args = parser.parse_args()
    asyncio.run(_run(args.username, args.password, args.department))
    return 0


if __name__ == "__main__":
    sys.exit(main())
