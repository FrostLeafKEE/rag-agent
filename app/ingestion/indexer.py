"""Milvus 索引器：幂等建 collection、写入/删除 chunks（PRD FR-13/14）。

权限标签（department）与文档元数据写入标量字段，供检索层 filter 下推（ADR-05）。
"""

from __future__ import annotations

import logging
import re
import zlib

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient
from pymilvus.milvus_client.index import IndexParams
from pymilvus.orm.schema import Function, FunctionType

from app.ingestion.models import DocumentChunk

logger = logging.getLogger(__name__)

# BGE-M3 稠密向量维度（FakeEmbedder 也默认 1024）
DEFAULT_DIM = 1024

# doc_id 白名单：防 Milvus filter 字符串拼接注入（如 `" || doc_id != "x` 清空全库）
# \w 在 unicode 模式下包含中文；允许 1~64 位字母数字下划线连字符
SAFE_DOC_ID = re.compile(r"^[\w\-]{1,64}$")


def build_schema(dim: int = DEFAULT_DIM) -> CollectionSchema:
    """chunks collection 的 schema（与 docs/TECH_STACK.md §4.1 对齐）。

    稀疏字段由 Milvus 内置 BM25 function 自动计算（写入时基于 content 生成），
    检索时直接传原文即可（混合检索的稀疏路，ADR-01）。
    """
    bm25 = Function(
        name="bm25",
        function_type=FunctionType.BM25,
        input_field_names=["content"],
        output_field_names=["sparse_vector"],
    )
    return CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="chunk_index", dtype=DataType.INT32),
            FieldSchema(name="page", dtype=DataType.INT32),
            FieldSchema(name="section_path", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="department", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params={"type": "chinese"},  # 中文分词（jieba），BM25 必需
            ),
            FieldSchema(name="updated_at", dtype=DataType.INT64),
        ],
        functions=[bm25],
    )


class MilvusIndexer:
    """封装 MilvusClient，负责 collection 生命周期与数据写入。"""

    def __init__(self, uri: str, collection: str, dim: int = DEFAULT_DIM) -> None:
        self._client = MilvusClient(uri=uri)
        self._collection = collection
        self._dim = dim

    def ensure_collection(self) -> None:
        """幂等创建 collection（含标量字段索引）；已存在时补齐缺失索引。"""
        if not self._client.has_collection(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                schema=build_schema(self._dim),
            )
            logger.info("已创建 collection：%s（dim=%s）", self._collection, self._dim)
        # 向量索引（load/查询的前置条件；AUTOINDEX 由 Milvus 自动选择算法）
        if not any("idx_vector" in idx for idx in self._client.list_indexes(self._collection)):
            params = IndexParams()
            params.add_index(
                field_name="vector",
                index_type="AUTOINDEX",
                metric_type="COSINE",
                index_name="idx_vector",
            )
            self._client.create_index(self._collection, params)
            logger.info("已创建向量索引：%s.idx_vector", self._collection)
        # BM25 稀疏索引（与 function 的 metric 一致）
        if not any("idx_sparse" in idx for idx in self._client.list_indexes(self._collection)):
            params = IndexParams()
            params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
                index_name="idx_sparse",
            )
            self._client.create_index(self._collection, params)
            logger.info("已创建稀疏索引：%s.idx_sparse", self._collection)
        # 查询/检索要求 collection 处于 loaded 状态（幂等）
        state = self._client.get_load_state(self._collection)
        state_val = state.get("state") if isinstance(state, dict) else getattr(state, "state", None)
        if state_val != 3:  # LoadState.Loaded
            self._client.load_collection(self._collection)
        # 权限过滤与文档管理常用的标量索引（幂等：缺失才建）
        existing = self._client.list_indexes(self._collection)
        for field in ("doc_id", "department", "updated_at"):
            if any(field in idx for idx in existing):
                continue
            params = IndexParams()
            params.add_index(field_name=field, index_type="INVERTED", index_name=f"idx_{field}")
            self._client.create_index(self._collection, params)
            logger.info("已创建标量索引：%s.%s", self._collection, field)

    def upsert(self, chunks: list[DocumentChunk], vectors: list[list[float]]) -> None:
        """批量写入（按 id 幂等，重复摄入不会产生重复块）。"""
        if not chunks:
            return
        for c in chunks:
            if not SAFE_DOC_ID.match(c.doc_id):
                raise ValueError(f"非法 doc_id：{c.doc_id!r}")
        rows = [
            {
                # 确定性 id：同文档同序号的块幂等（hash() 跨进程不稳定，用 crc32）
                "id": zlib.crc32(f"{c.doc_id}:{c.chunk_index}".encode()) & 0x7FFFFFFF,
                "vector": vector,
                "doc_id": c.doc_id,
                "chunk_index": c.chunk_index,
                "page": c.page or 0,
                "section_path": c.section_path,
                "department": c.department,
                "content": c.content,
                "updated_at": c.updated_at,
            }
            for c, vector in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(collection_name=self._collection, data=rows)
        # flush 保证写入立即可查（Milvus 默认异步可见）
        self._client.flush(self._collection)

    def delete_by_doc(self, doc_id: str) -> None:
        """删除某文档的全部 chunks（文档更新/失效时调用，FR-04/06）。

        doc_id 必须通过白名单校验（防 filter 表达式注入）。
        """
        if not SAFE_DOC_ID.match(doc_id):
            raise ValueError(f"非法 doc_id：{doc_id!r}")
        self._client.delete(
            collection_name=self._collection,
            filter=f'doc_id == "{doc_id}"',
        )
        self._client.flush(self._collection)

    def count(self) -> int:
        return self._client.get_collection_stats(self._collection).get("row_count", 0)

    # ---------- 检索 ----------

    _OUTPUT_FIELDS = [
        "id",
        "doc_id",
        "chunk_index",
        "page",
        "section_path",
        "department",
        "content",
    ]

    def _search(self, *, anns_field: str, data: list, top_k: int, filter_expr: str) -> list[dict]:
        """统一检索入口，返回命中的实体字典列表（含 distance）。"""
        search_params: dict = {}
        if anns_field == "sparse_vector":
            search_params = {"params": {"drop_ratio_search": 0.2}}
        hits = self._client.search(
            collection_name=self._collection,
            data=data,
            anns_field=anns_field,
            limit=top_k,
            filter=filter_expr or None,
            output_fields=self._OUTPUT_FIELDS,
            search_params=search_params,
        )
        return hits[0]

    def search_dense(self, vector: list[float], top_k: int, filter_expr: str = "") -> list[dict]:
        """稠密检索：BGE-M3 向量余弦相似度。"""
        return self._search(
            anns_field="vector", data=[vector], top_k=top_k, filter_expr=filter_expr
        )

    def search_sparse(self, text: str, top_k: int, filter_expr: str = "") -> list[dict]:
        """稀疏检索：Milvus 内置 BM25（传入原文，function 自动转换）。"""
        return self._search(
            anns_field="sparse_vector", data=[text], top_k=top_k, filter_expr=filter_expr
        )
