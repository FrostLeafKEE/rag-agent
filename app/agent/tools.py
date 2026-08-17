"""Agent 工具注册框架（FR-25，P2 W1）。

工具扩展点：注册一个 Tool 即可被 plan_tools 节点发现并按需调用。
当前内置工具 query_documents：只读 SQL 查询文档元数据表，用于统计/状态类问题。

安全边界（只读 SQL）：去注释 → 拒多语句 → 仅 SELECT → 表名白名单 → 强制 LIMIT → 值截断。
接入真实业务表时扩展白名单即可，列级权限另评审。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# 只读 SQL 约束
_SQL_TABLE_WHITELIST = {"documents"}
_MAX_VALUE_CHARS = 200

# 捕获 FROM/JOIN 后的表名列表（含逗号分隔；遇到 WHERE/ON 等关键字自然停止）。
# 修复 FIX P0-2：`FROM documents, users` 逗号分隔的后续表名此前未被校验。
_SQL_FROM_CLAUSE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*\s*(?:,\s*[A-Za-z_][A-Za-z0-9_]*\s*)*)"
)

logger = logging.getLogger(__name__)


def _safe_select(sql: str, max_rows: int) -> str:
    """校验并清洗只读 SELECT；不合法抛 ValueError（由 ToolRegistry.call 捕获）。"""
    cleaned = re.sub(r"--.*$", "", sql, flags=re.M)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.S).strip()
    if not cleaned:
        raise ValueError("空查询")
    if ";" in cleaned:
        raise ValueError("禁止多语句查询")
    if not cleaned.upper().startswith("SELECT"):
        raise ValueError("只允许 SELECT 查询")
    for clause in _SQL_FROM_CLAUSE.finditer(cleaned):
        for table in re.split(r"\s*,\s*", clause.group(1).strip()):
            if table not in _SQL_TABLE_WHITELIST:
                raise ValueError(f"表 {table} 不在白名单内")
    if "LIMIT" not in cleaned.upper():
        cleaned += f" LIMIT {max_rows}"
    return cleaned


def _truncate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
                row[key] = value[:_MAX_VALUE_CHARS] + "…"
    return rows


async def _query_documents(args: dict, max_rows: int) -> dict:
    """执行只读查询（函数内 import，便于测试 patch session_factory）。"""
    sql = (args.get("sql") or "").strip()
    cleaned = _safe_select(sql, max_rows)
    from sqlalchemy import text

    from app.db import session_factory  # 调用时解析，测试可覆盖

    async with session_factory() as session:
        result = await session.execute(text(cleaned))
        rows = [dict(row) for row in result.mappings().all()]
    return {"rows": _truncate_rows(rows), "count": len(rows)}


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict[str, Any]
    fn: Callable[[dict, int], Awaitable[dict]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def describe(self) -> str:
        """工具清单文本（供 plan_tools 提示词展示）。"""
        lines = []
        for tool in self._tools.values():
            schema = json.dumps(tool.args_schema, ensure_ascii=False)
            lines.append(f"- {tool.name}：{tool.description}\n  参数：{schema}")
        return "\n".join(lines)

    async def call(self, name: str, args: dict) -> dict:
        """调用工具；一律返回 {ok, result|error} 结构，不向图抛异常。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"未知工具 {name}"}
        try:
            from app.config import get_settings

            result = await tool.fn(args, get_settings().tools_max_rows)
            return {"ok": True, "result": result}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:  # noqa: BLE001
            logger.warning("工具 %s 调用失败", name, exc_info=True)
            return {"ok": False, "error": "工具执行异常"}


tool_registry = ToolRegistry()
tool_registry.register(
    Tool(
        name="query_documents",
        description=(
            "只读查询知识库文档元数据表 documents"
            "（列：title 标题、source_name 原始文件名、department 部门、status 状态"
            "、chunk_count 分块数、uploaded_by 上传者、created_at 上传时间），"
            "用于统计、状态类问题，如“知识库有多少篇文档”“某部门文档数量”。"
            "自动限制最多 50 行。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "只读 SELECT SQL，仅可查询 documents 表，自动追加 LIMIT",
                }
            },
            "required": ["sql"],
        },
        fn=_query_documents,
    )
)