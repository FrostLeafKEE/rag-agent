"""数据清洗与质量门禁模块（RAG 摄入管道增强）。

管道位置：分块后 → 【规则清洗】→ 【LLM 清洗（可选）】→ PII 脱敏 → 嵌入。
原则：规则优先，LLM 兜底；LLM 输出必须通过模型原生 Structured Output 强约束
（FMA 语义写入 Pydantic schema，而非仅依赖 prompt）。

- 与 PII 脱敏完全解耦（顺序上先清洗后脱敏，本模块不 import 脱敏逻辑）
- 不依赖 Docling / Milvus / 检索层；输入输出为块对象（ParsedBlock / DocumentChunk）
- 内容去重基于清洗后全文 sha1，doc_id 级幂等逻辑保持不变
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------- 规则级清洗 ----------------

# 页眉页脚模式：中英文页码、编号 + 尾部页码、纯页码行
_PATTERNS_HEADER_FOOTER = [
    re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页"),
    re.compile(r"page\s*\d+\s*of\s*\d+", re.IGNORECASE),
    re.compile(r"^\s*\d{1,3}\s*$"),  # 纯页码行
]
# 目录条目模式："1.1 标题 …… 3" / "1.1 标题 .... 5"
_PATTERN_TOC = re.compile(
    r"^\s*\d+(\.\d+)*\s+[\u4e00-\u9fffA-Za-z][^\n]{0,40}"
    r"(?:[.·…]{2,}|\s+\.{2,}\s+)\s*\d{1,3}\s*$"
)
# 乱码/OCR 特征：替换字符、控制字符、mojibake 常见序列
_PATTERN_GARBLED = re.compile(r"[\ufffd\x00-\x08\x0e-\x1f]|Ã[\x80-\xbf]|â€[^\n]*")


def _text_of(block: Any) -> str:
    """取块文本：兼容 ParsedBlock（.text）与 DocumentChunk（.content）。"""
    text = getattr(block, "text", None) or getattr(block, "content", "")
    return text or ""


def _is_header_footer_line(line: str) -> bool:
    return any(p.search(line) for p in _PATTERNS_HEADER_FOOTER)


def _is_toc_line(line: str) -> bool:
    return bool(_PATTERN_TOC.match(line)) or bool(
        "……" in line and re.search(r"\d\s*$", line)
    )


def noise_reason(text: str) -> str | None:
    """判定文本的噪声类型；无噪声返回 None。

    返回原因标识：too_short / no_language / header_footer / toc。
    页眉页脚/目录为**行级**模式：多行块仅当所有非空行都是该类噪声行时
    才判为噪声（正常段落中混入一行页码不丢块）。
    """
    if not text.strip():
        return "too_short"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        if all(_is_header_footer_line(line) for line in lines):
            return "header_footer"
        if all(_is_toc_line(line) for line in lines):
            return "toc"
    else:
        if _is_header_footer_line(lines[0]):
            return "header_footer"
        if _is_toc_line(lines[0]):
            return "toc"
    stripped = re.sub(r"\s+", "", text)
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", stripped):
        return "no_language"
    if len(stripped) < 10:
        return "too_short"
    return None


def filter_noise_block(block: Any) -> Any | None:
    """规则清洗：噪声块返回 None（丢弃），否则返回原块。

    副作用：丢弃时写 INFO 日志（原因 + 文本前 40 字符）。
    """
    text = _text_of(block)
    reason = noise_reason(text)
    if reason is not None:
        logger.info("[NOISE_DROP] reason=%s text=%r", reason, text[:40])
        return None
    return block


# ---------------- 内容 hash 去重 ----------------


def content_hash_of(text: str) -> str:
    """清洗后文本的 sha1（内容级去重键）。"""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def dedup_by_content(
    block: Any,
    existing_hashes: set[str],
    current_hashes: set[str],
) -> tuple[Any | None, str]:
    """内容去重：与已入库 + 本批 hash 集合比对，重复返回 (None, hash)。

    返回 (保留块 | None, sha1)。保留块时调用方须把 hash 加入 current_hashes。
    """
    digest = content_hash_of(_text_of(block))
    if digest in existing_hashes or digest in current_hashes:
        logger.info("[DEDUP_SKIP] hash=%s text=%r", digest, _text_of(block)[:40])
        return None, digest
    return block, digest


# ---------------- LLM 清洗（强格式输出） ----------------


class CleaningResult(BaseModel):
    """LLM 清洗结果——结构化输出的 schema source of truth（FMA 硬约束在字段层）。

    F-保真：不得修改事实/数字/术语（描述即约束，模型原生结构化输出会强制遵循）；
    M-语义完整：不得删除有语义的句子；
    A-属性保留：保持原始结构类型（如表格/标题语义）。
    """

    cleaned_text: str = Field(
        ...,
        min_length=1,
        description="清洗后的文本。F-保真：不得修改事实/数字/术语；"
        "M-语义完整：不得删除有语义的句子；A-属性保留：保持原始结构类型",
    )
    changes: list[str] = Field(
        default_factory=list,
        description="变更说明列表，每条 ≤50 字",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="清洗置信度，<0.7 时调用方应回退到原文",
    )


def _looks_garbled(text: str) -> bool:
    """OCR 来源 / 乱码特征判断（LLM 清洗触发条件之一）。"""
    return bool(_PATTERN_GARBLED.search(text))


def validate_llm_cleaning(result: CleaningResult, original_text: str) -> CleaningResult | None:
    """后处理语义校验（结构化输出已保证结构合法，此处只做语义级检查）。

    返回 None 时调用方使用原文，并记录 [LLM_CLEAN_FALLBACK] 日志。
    """
    ratio = len(result.cleaned_text) / max(len(original_text), 1)
    if ratio < 0.5:
        logger.warning("[LLM_CLEAN_FALLBACK] reason=length_drop, ratio=%.2f", ratio)
        return None
    if result.confidence < 0.7:
        logger.warning(
            "[LLM_CLEAN_FALLBACK] reason=low_confidence, conf=%.2f",
            result.confidence,
        )
        return None
    return result


# ---------------- 管道入口 ----------------


def clean_chunks(
    chunks: list[Any],
    existing_hashes: set[str] | None = None,
    enable_llm: bool = False,
    llm_min_len: int = 20,
) -> tuple[list[Any], dict[str, Any], set[str]]:
    """清洗管道入口：规则清洗 → LLM 清洗（可选）→ 内容去重。

    输入：chunk 列表（ParsedBlock 或 DocumentChunk，均有文本字段）。
    返回：(保留块, 统计报告 dict, 本批新 hash 集合)。

    统计字段：total / filtered / dedup_skipped / avg_chunk_length /
    empty_pages / llm_cleaned / llm_fallback / noise_reasons（原因计数）。
    empty_pages 口径：某页的所有块均被过滤则计 1（按块 page 字段分组）。
    """
    existing = set(existing_hashes or [])
    current: set[str] = set()
    kept: list[Any] = []
    stats: dict[str, Any] = {
        "total": len(chunks),
        "filtered": 0,
        "dedup_skipped": 0,
        "avg_chunk_length": 0.0,
        "empty_pages": 0,
        "llm_cleaned": 0,
        "llm_fallback": 0,
        "noise_reasons": {},
    }
    filtered_pages: dict[int | None, int] = {}
    total_pages: dict[int | None, int] = {}

    for chunk in chunks:
        page = getattr(chunk, "page", None)
        total_pages[page] = total_pages.get(page, 0) + 1
        text = _text_of(chunk)

        reason = noise_reason(text)
        if reason is not None:
            stats["filtered"] += 1
            stats["noise_reasons"][reason] = stats["noise_reasons"].get(reason, 0) + 1
            filtered_pages[page] = filtered_pages.get(page, 0) + 1
            continue

        if enable_llm and len(text) >= llm_min_len and _looks_garbled(text):
            cleaned = _try_llm_clean(chunk)
            if cleaned is not None:
                stats["llm_cleaned"] += 1
            else:
                stats["llm_fallback"] += 1

        kept_chunk, digest = dedup_by_content(chunk, existing, current)
        if kept_chunk is None:
            stats["dedup_skipped"] += 1
            filtered_pages[page] = filtered_pages.get(page, 0) + 1
            continue
        current.add(digest)
        kept.append(kept_chunk)

    stats["avg_chunk_length"] = round(sum(len(_text_of(c)) for c in kept) / max(len(kept), 1), 1)
    stats["empty_pages"] = sum(
        1 for p, n in total_pages.items() if n > 0 and filtered_pages.get(p, 0) == n
    )
    return kept, stats, current


def _try_llm_clean(block: Any) -> bool:
    """LLM 清洗（强格式）：成功则原地替换块文本，失败返回 False（调用方记 fallback）。

    结构化输出优先级：OpenAI response_format(json_schema) → LangChain
    with_structured_output → prompt 兜底（标注 FALLBACK）。
    """
    original = _text_of(block)
    result = _structured_clean(original)
    if result is None:
        return False
    _set_text(block, result.cleaned_text)
    logger.info(
        "[LLM_CLEAN] len=%d→%d changes=%s", len(original), len(result.cleaned_text), result.changes
    )
    return True


def _structured_clean(text: str) -> CleaningResult | None:
    """按优先级尝试三种结构化输出路径；全部失败返回 None（原文兜底）。"""
    result = _clean_via_response_format(text)
    if result is not None:
        return result
    result = _clean_via_structured_output(text)
    if result is not None:
        return result
    # FALLBACK: model does not support structured output
    result = _clean_via_prompt_fallback(text)
    return result


def _clean_via_response_format(text: str) -> CleaningResult | None:
    """首选：OpenAI response_format json_schema（Pydantic schema 即 source of truth）。"""
    try:
        from app.llm.gateway import get_llm

        llm = get_llm()
        schema = CleaningResult.model_json_schema()
        raw = llm.complete(
            [{"role": "user", "content": _LLM_CLEAN_PROMPT.format(text=text)}],
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "CleaningResult", "schema": schema},
            },
        )
        data = json.loads(_extract_json(raw))
        return validate_llm_cleaning(CleaningResult.model_validate(data), text)
    except Exception:  # noqa: BLE001
        logger.warning("response_format 路径不可用，降级", exc_info=True)
        return None


def _clean_via_structured_output(text: str) -> CleaningResult | None:
    """次选：LangChain with_structured_output（Pydantic model 驱动）。"""
    try:
        from langchain_openai import ChatOpenAI

        from app.config import get_settings

        settings = get_settings()
        llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,  # type: ignore  # langchain 期望 SecretStr
            model=settings.llm_model,
            temperature=0,
        ).with_structured_output(CleaningResult)
        result = llm.invoke(_LLM_CLEAN_PROMPT.format(text=text))
        if isinstance(result, CleaningResult):
            return validate_llm_cleaning(result, text)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("with_structured_output 路径不可用，降级 FALLBACK", exc_info=True)
        return None


def _clean_via_prompt_fallback(text: str) -> CleaningResult | None:
    """兜底：prompt 约束 + 后处理校验（模型不支持 structured output 时）。"""
    try:
        from app.llm.gateway import get_llm

        llm = get_llm()
        raw = llm.complete(
            [{"role": "user", "content": _LLM_CLEAN_PROMPT.format(text=text)}],
            temperature=0,
        )
        data = json.loads(_extract_json(raw))
        return validate_llm_cleaning(CleaningResult.model_validate(data), text)
    except Exception:  # noqa: BLE001
        logger.warning("[LLM_CLEAN_FALLBACK] reason=parse_error", exc_info=True)
        return None


def _extract_json(raw: str) -> str:
    """从 LLM 输出中收紧到 JSON 主体（兼容 ```json 包裹/前后解释）。"""
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = raw.find(opening), raw.rfind(closing)
        if start != -1 and end > start:
            return raw[start : end + 1]
    return raw


def _set_text(block: Any, text: str) -> None:
    """原位替换块文本（兼容 ParsedBlock.text / DocumentChunk.content）。"""
    if hasattr(block, "text"):
        block.text = text
    if hasattr(block, "content"):
        block.content = text


_LLM_CLEAN_PROMPT = (
    "你是文档清洗助手。修复下面的文本中的乱码、OCR 错误与噪声，但必须严格遵守：\n"
    "1. F-保真：不得修改事实、数字、专有名词与术语；\n"
    "2. M-语义完整：不得删除任何有语义的句子；\n"
    "3. A-属性保留：保持原结构类型（表格/标题/正文）。\n"
    '输出 JSON：{{"cleaned_text": "清洗后文本", "changes": ["变更说明"], '
    '"confidence": 0.0~1.0}}。\n\n'
    "原文：\n{text}"
)


def build_ingestion_report(stats: dict[str, Any]) -> dict[str, Any]:
    """把 clean_chunks 的统计序列化为可落库/可返回的报告结构。

    输入：clean_chunks 返回的 stats；输出：可直接写 IngestionReport 的字段映射。
    """
    return {
        "total_blocks": stats["total"],
        "filtered_blocks": stats["filtered"],
        "dedup_skipped": stats["dedup_skipped"],
        "avg_chunk_length": stats["avg_chunk_length"],
        "empty_pages": stats["empty_pages"],
        "noise_reasons": json.dumps(stats["noise_reasons"], ensure_ascii=False),
        "llm_cleaned": stats["llm_cleaned"],
        "llm_fallback": stats["llm_fallback"],
    }
