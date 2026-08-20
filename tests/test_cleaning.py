"""数据清洗与质量门禁测试（P2 增强）。

覆盖：filter_noise_block 四类噪声 / dedup_by_content 去重 / validate_llm_cleaning
回退 / clean_chunks 集成统计。
"""

from __future__ import annotations

from app.ingestion.cleaning import (
    CleaningResult,
    clean_chunks,
    content_hash_of,
    dedup_by_content,
    filter_noise_block,
    noise_reason,
    validate_llm_cleaning,
)
from app.ingestion.models import DocumentChunk, ParsedBlock


def _chunk(content: str, page: int | None = 1) -> DocumentChunk:
    return DocumentChunk(doc_id="D", chunk_index=0, content=content, page=page)


# ---- filter_noise_block：正常块保留 ----


def test_filter_keeps_normal_block() -> None:
    block = _chunk("权限过滤是在检索层执行的，部门标签随查询下推到向量库。")
    assert filter_noise_block(block) is block


def test_filter_accepts_parsed_block() -> None:
    """鸭子类型：ParsedBlock（.text）同样支持。"""
    block = ParsedBlock(text="这是一个正常的标题内容段落，包含足够的信息量。")
    assert filter_noise_block(block) is block


# ---- filter_noise_block：四类噪声丢弃 ----


def test_filter_drops_blank_block() -> None:
    assert filter_noise_block(_chunk("   \n  \t ")) is None
    assert noise_reason("   \n  ") == "too_short"


def test_filter_drops_short_block() -> None:
    assert filter_noise_block(_chunk("abc")) is None
    assert noise_reason("abc") == "too_short"


def test_filter_drops_symbols_only_block() -> None:
    """纯符号/纯数字编号（无 CJK 与字母）→ no_language。"""
    assert filter_noise_block(_chunk("※※※※ ※※※※")) is None
    assert noise_reason("1. 2. 3. 4.") == "no_language"


def test_filter_drops_header_footer() -> None:
    assert filter_noise_block(_chunk("第 3 页 共 12 页")) is None
    assert noise_reason("第 3 页 共 12 页") == "header_footer"
    assert noise_reason("Page 5 of 20") == "header_footer"
    assert noise_reason("  7  ") == "header_footer"


def test_filter_drops_toc_entry() -> None:
    assert filter_noise_block(_chunk("1.1 简介 …… 3")) is None
    assert noise_reason("1.1 简介 …… 3") == "toc"
    assert noise_reason("3.2 技术选型 .... 15") == "toc"


def test_filter_keeps_multiline_block_with_pageno() -> None:
    """正常段落中混入一行页码 → 整块保留（行级判定，不误杀）。"""
    block = _chunk("权限过滤在检索层执行，防止越权读取。\n第 3 页 共 12 页")
    assert filter_noise_block(block) is block
    assert noise_reason("权限过滤在检索层执行。\n第 3 页 共 12 页") is None


def test_filter_drops_multiline_pure_footer() -> None:
    """多行全是页脚 → 判 header_footer。"""
    assert noise_reason("第 1 页 共 5 页\n第 2 页 共 5 页") == "header_footer"


# ---- dedup_by_content ----


def test_dedup_same_content_different_doc() -> None:
    """A.docx 与 A.pdf 内容相同 → 后到者跳过（内容级去重）。"""
    existing = {content_hash_of("完全相同的正文内容")}
    block = _chunk("完全相同的正文内容")
    kept, digest = dedup_by_content(block, existing, set())
    assert kept is None
    assert digest == content_hash_of("完全相同的正文内容")


def test_dedup_different_content_kept() -> None:
    existing = {content_hash_of("其他文档的内容")}
    block = _chunk("本文档的独有内容")
    kept, digest = dedup_by_content(block, existing, set())
    assert kept is block
    assert digest == content_hash_of("本文档的独有内容")


def test_dedup_same_batch_skipped() -> None:
    """同批内重复块也跳过（current_hashes 参与比对）。"""
    block = _chunk("重复出现的段落")
    digest = content_hash_of("重复出现的段落")
    kept, _ = dedup_by_content(block, set(), {digest})
    assert kept is None


# ---- validate_llm_cleaning ----


def test_validate_accepts_good_result() -> None:
    result = CleaningResult(cleaned_text="清洗后的完整文本内容", changes=["去噪"], confidence=0.9)
    original = "清洗前的原文，长度足够，语义完整。"
    assert validate_llm_cleaning(result, original) is result


def test_validate_rejects_length_drop() -> None:
    result = CleaningResult(cleaned_text="短", changes=[], confidence=0.95)
    original = "这是一段非常长的原文，包含大量需要保留的语义信息，远超结果长度的一半以上。"
    assert validate_llm_cleaning(result, original) is None


def test_validate_rejects_low_confidence() -> None:
    result = CleaningResult(cleaned_text="内容长度足够但置信度低", changes=[], confidence=0.5)
    assert validate_llm_cleaning(result, "内容长度足够但置信度低的原文") is None


# ---- clean_chunks 集成：统计与管道 ----


def test_clean_chunks_stats_and_filtering() -> None:
    chunks = [
        _chunk("正常内容：权限过滤在检索层执行，防止越权读取。", page=1),
        _chunk("第 2 页 共 10 页", page=1),  # header_footer 丢弃
        _chunk("1.1 目录条目 …… 5", page=2),  # toc 丢弃
        _chunk("正常内容：混合检索由稠密与稀疏两条路径组成。", page=2),
        _chunk("正常内容：回答带引用溯源。", page=2),
    ]
    kept, stats, new_hashes = clean_chunks(chunks, existing_hashes=None)
    assert len(kept) == 3
    assert stats["total"] == 5
    assert stats["filtered"] == 2
    assert stats["noise_reasons"] == {"header_footer": 1, "toc": 1}
    assert stats["avg_chunk_length"] > 0
    assert len(new_hashes) == 3


def test_clean_chunks_dedup_against_existing() -> None:
    """existing_hashes 命中 → 块被跳过且计入 dedup_skipped。"""
    dup_text = "与已入库文档完全相同的段落内容"
    chunks = [
        _chunk(dup_text),
        _chunk("另一段正常内容，包含足够信息。"),
    ]
    kept, stats, _ = clean_chunks(chunks, existing_hashes={content_hash_of(dup_text)})
    assert len(kept) == 1
    assert stats["dedup_skipped"] == 1


def test_clean_chunks_empty_pages_detection() -> None:
    """某页全部块被过滤 → empty_pages 计数。"""
    chunks = [
        _chunk("第 1 页 共 3 页", page=1),
        _chunk("   ", page=1),  # 页 1 全被过滤
        _chunk("正常内容块，长度足够，语义完整。", page=2),
    ]
    kept, stats, _ = clean_chunks(chunks)
    assert len(kept) == 1
    assert stats["empty_pages"] == 1


def test_clean_chunks_llm_disabled_by_default() -> None:
    """enable_llm=False 时不触发 LLM（无外部调用）。"""
    chunks = [_chunk("正常内容块，长度足够。")]
    kept, stats, _ = clean_chunks(chunks, enable_llm=False)
    assert len(kept) == 1
    assert stats["llm_cleaned"] == 0
    assert stats["llm_fallback"] == 0
