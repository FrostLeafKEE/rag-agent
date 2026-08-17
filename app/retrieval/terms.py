"""术语表/同义词（P2 W5，P1_PLAN 2.2 遗留）：别名 → 标准词 映射参与检索。

config/terms.json 结构：{"别名": "标准词", ...}
应用方式：agent 图的 term_expand 节点在主查询（改写后的 queries[0]）上生成
"替换后的变体"加入查询集合（最长匹配优先，原查询保留），变体与原文一起去
混合检索，提升口语/别名问法下的中文召回。

示例映射（企业自配）：
  {"发版": "发布", "离职": "注销", "上线": "发布"}
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def load_terms(config_path: str = "config/terms.json") -> dict[str, str]:
    """读取术语表 {别名: 标准词}；文件缺失/解析失败返回空表。"""
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("terms.json 解析失败：%s", path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in data.items() if k and v}


def expand_terms(query: str, config_path: str = "config/terms.json") -> list[str]:
    """返回术语替换后的检索变体（无命中时为空列表）。

    最长匹配优先（"账号注销"先于"注销"），一条 query 生成一个变体（全部替换），
    变体与原查询相同时返回空。
    """
    terms = load_terms(config_path)
    if not terms or not query:
        return []
    aliases = sorted(
        ((alias, target) for alias, target in terms.items() if alias in query),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    if not aliases:
        return []
    rewritten = query
    for alias, target in aliases:
        rewritten = rewritten.replace(alias, target)
    if rewritten == query:
        return []
    return [rewritten]
