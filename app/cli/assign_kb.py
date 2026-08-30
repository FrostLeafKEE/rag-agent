"""批量归组小工具：把存量文档归入指定知识库（文档管理「知识库」层配套）。

用法：
  # 把某部门的未分组文档全部归入指定知识库
  uv run python -m app.cli.assign_kb --kb "研发部知识库" --department 研发部

  # 按文档 ID 精确归组（可多个）
  uv run python -m app.cli.assign_kb --kb "研发部知识库" \
      --doc-id release-norm-v21 --doc-id sec-test-doc

  # 查看当前未分组文档
  uv run python -m app.cli.assign_kb --list-unassigned

规则：
- 文档归入知识库后，department 自动同步为该库的部门（权限继承）；
- kb 可传名称或数字 ID；目标库不存在时报错。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.db import session_factory
from app.models import Document, KnowledgeBase

logger = logging.getLogger(__name__)


async def _resolve_kb(session, identifier: str) -> KnowledgeBase:
    """按名称或数字 ID 解析知识库。"""
    if identifier.isdigit():
        kb = await session.get(KnowledgeBase, int(identifier))
        if kb is not None:
            return kb
    kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.name == identifier))
    if kb is None:
        raise SystemExit(f"知识库不存在：{identifier}")
    return kb


async def assign(
    kb_identifier: str,
    doc_ids: list[str] | None = None,
    department: str | None = None,
    all_unassigned: bool = False,
) -> None:
    async with session_factory() as session:
        kb = await _resolve_kb(session, kb_identifier)
        query = select(Document).where(Document.kb_id.is_(None))
        if doc_ids:
            query = query.where(Document.doc_id.in_(doc_ids))
        elif department:
            query = query.where(Document.department == department)
        elif not all_unassigned:
            raise SystemExit("请指定 --doc-id / --department / --all-unassigned 之一")

        docs = list(await session.scalars(query))
        if not docs:
            print("没有符合条件的未分组文档")
            return
        for doc in docs:
            doc.kb_id = kb.id
            doc.department = kb.department  # 权限继承库的部门
            print(f"  ✓ {doc.doc_id} → {kb.name}")
        await session.commit()
        print(f"已归组 {len(docs)} 篇 → {kb.name}（部门：{kb.department}）")


async def list_unassigned() -> None:
    async with session_factory() as session:
        docs = list(await session.scalars(select(Document).where(Document.kb_id.is_(None))))
        if not docs:
            print("所有文档均已分组")
            return
        print(f"未分组文档 {len(docs)} 篇：")
        for doc in docs:
            print(f"  {doc.doc_id}  {doc.department or '—'}  {doc.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="批量归组文档到知识库")
    parser.add_argument("--kb", help="目标知识库（名称或 ID）")
    parser.add_argument("--doc-id", action="append", help="按文档 ID 归组（可多次）")
    parser.add_argument(
        "--department", help="归组该部门的所有未分组文档"
    )
    parser.add_argument("--all-unassigned", action="store_true", help="归组所有未分组文档")
    parser.add_argument("--list-unassigned", action="store_true", help="列出未分组文档")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    async def run() -> None:
        if args.list_unassigned:
            await list_unassigned()
            return
        if not args.kb:
            raise SystemExit("请指定目标知识库：--kb <名称或ID>")
        await assign(args.kb, args.doc_id, args.department, args.all_unassigned)

    asyncio.run(run())


if __name__ == "__main__":
    main()
