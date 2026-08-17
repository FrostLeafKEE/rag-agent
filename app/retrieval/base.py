"""检索层接口抽象与数据模型（ADR-03：检索原语自管，不依赖框架）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 部门名白名单：防 Milvus filter 注入（FIX P0-1）。
# 允许中文/字母/数字/下划线/连字符/空格；拒绝引号、反斜杠、括号等表达式字符。
SAFE_DEPARTMENT = re.compile(r"^[\w\u4e00-\u9fff \-]{1,64}$")


@dataclass
class RetrievedChunk:
    """一次检索返回的候选块（混合融合或重排后）。"""

    chunk_id: int
    doc_id: str
    chunk_index: int
    content: str
    section_path: str = ""
    page: int | None = None
    department: str = ""
    score: float = 0.0  # 融合分（RRF）或重排分

    @classmethod
    def from_milvus_hit(cls, hit: dict) -> RetrievedChunk:
        entity = hit["entity"]
        return cls(
            chunk_id=entity["id"],
            doc_id=entity["doc_id"],
            chunk_index=entity["chunk_index"],
            content=entity["content"],
            section_path=entity.get("section_path", ""),
            page=entity.get("page") or None,
            department=entity.get("department", ""),
            score=float(hit.get("distance", 0.0)),
        )


def build_filter(departments: list[str] | None) -> str:
    """构造权限过滤表达式（ADR-05：检索层 filter 下推）。

    None 表示不限制（管理员）；空列表表示"零可见"（未分配部门的普通用户），
    必须产出永假表达式而非空串，否则会绕过 RBAC 拿全库数据。

    部门名必须通过白名单校验（防 Milvus filter 字符串注入，FIX P0-1）：
    仅允许中文/字母/数字/下划线/连字符/空格，最长 64。
    """
    if departments is None:
        return ""
    if not departments:
        return "1 == 0"
    for dept in departments:
        if not SAFE_DEPARTMENT.match(dept):
            raise ValueError(f"非法部门名：{dept!r}")
    quoted = ", ".join(f'"{d}"' for d in departments)
    return f"department in [{quoted}]"
