"""端到端验证：W1 工具调用（FR-25）。

- 统计类问题 → plan_tools 应选中 query_documents 并拿到真实行数
- 普通问答 → 不应触发工具
运行：uv run python scripts/e2e_tools_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph import agent_graph  # noqa: E402

CASES = [
    ("stats", "知识库中一共有多少篇文档？"),
    ("stats", "哪个部门的文档数量最多？"),
    ("normal", "权限过滤是怎么在检索层实现的？"),
]


async def main() -> None:

    for kind, question in CASES:
        result = await agent_graph.ainvoke({"question": question, "top_k": 5})
        tools = result.get("tool_results") or []
        ok_tools = [t for t in tools if t.get("ok")]
        print(f"\n=== {kind}：{question}")
        print(f"intent={result.get('intent')} tools={len(tools)}")
        for t in tools:
            print(
                f"  tool={t['tool']} ok={t['ok']} "
                f"result={t.get('result') or t.get('error')}"
            )
        if ok_tools:
            has_context = "结构化数据" in result["messages"][0]["content"]
            print(f"  结构化数据已注入生成上下文：{has_context}")
        if kind == "stats" and not ok_tools:
            print("  [FAIL] 统计类问题未触发工具")
            sys.exit(1)
        if kind == "normal" and tools:
            print("  [FAIL] 普通问答误触发工具")
            sys.exit(1)
    print("\nPASS：工具调用端到端验证通过")


if __name__ == "__main__":
    asyncio.run(main())