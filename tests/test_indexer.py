"""摄入管道 → Milvus 集成测试（Milvus 不可达时自动跳过）。

使用独立 collection test_chunks，测试后清理。
"""

import pytest

from app.config import get_settings
from app.ingestion.chunker import chunk_document
from app.ingestion.embedder import FakeEmbedder
from app.ingestion.indexer import MilvusIndexer
from app.ingestion.models import DocumentChunk, ParsedBlock

TEST_COLLECTION = "test_chunks"


def _milvus_available() -> bool:
    try:
        from pymilvus import MilvusClient

        MilvusClient(uri=get_settings().milvus_uri).list_collections()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _milvus_available(), reason="Milvus 不可达")


@pytest.fixture()
def indexer() -> MilvusIndexer:
    indexer = MilvusIndexer(get_settings().milvus_uri, TEST_COLLECTION, FakeEmbedder().dim)
    indexer.ensure_collection()
    yield indexer
    indexer._client.drop_collection(TEST_COLLECTION)


def test_ingest_and_search_roundtrip(indexer: MilvusIndexer) -> None:
    blocks = [
        ParsedBlock(text="部署要求", level=1, kind="heading"),
        ParsedBlock(text="A 系统需要 Windows Server 2022 与 16GB 内存。"),
    ]
    chunks = chunk_document(
        blocks, doc_id="itest-doc", chunk_size=800, overlap=0, department="测试部"
    )
    embedder = FakeEmbedder()
    indexer.upsert(chunks, embedder.embed([c.content for c in chunks]))

    # 标量过滤：按 doc_id 与权限标签
    rows = indexer._client.query(
        TEST_COLLECTION,
        filter='doc_id == "itest-doc" and department == "测试部"',
        output_fields=["doc_id", "chunk_index", "section_path", "content"],
    )
    assert len(rows) == 1
    assert rows[0]["section_path"] == "部署要求"
    assert "Windows Server 2022" in rows[0]["content"]

    # 向量检索：同文本应命中原块
    hits = indexer._client.search(
        TEST_COLLECTION,
        data=[embedder.embed_one(chunks[0].content)],
        anns_field="vector",
        limit=1,
        output_fields=["doc_id"],
    )
    assert hits[0][0]["entity"]["doc_id"] == "itest-doc"


def test_upsert_is_idempotent(indexer: MilvusIndexer) -> None:
    chunk = DocumentChunk(
        doc_id="itest-doc", chunk_index=0, content="同一内容", department="测试部"
    )
    embedder = FakeEmbedder()
    vector = embedder.embed([chunk.content])
    indexer.upsert([chunk], vector)
    indexer.upsert([chunk], vector)  # 重复摄入
    rows = indexer._client.query(
        TEST_COLLECTION, filter='doc_id == "itest-doc"', output_fields=["id"]
    )
    assert len(rows) == 1


def test_delete_by_doc(indexer: MilvusIndexer) -> None:
    chunk = DocumentChunk(
        doc_id="to-delete", chunk_index=0, content="将被删除", department="测试部"
    )
    embedder = FakeEmbedder()
    indexer.upsert([chunk], embedder.embed([chunk.content]))
    indexer.delete_by_doc("to-delete")
    rows = indexer._client.query(
        TEST_COLLECTION, filter='doc_id == "to-delete"', output_fields=["id"]
    )
    assert rows == []
