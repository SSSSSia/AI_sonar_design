"""Milvus chunk 向量库封装（T3.2，§6 ⑤）。

collection `kb_chunks`：chunk_key(doc_id:chunk_index 主键) + vector(bge-m3 1024 维) +
doc_id/location/text。增量语义：按 doc_id 先删后插；检索支持 top-k。不可用时如实降级。
"""

from __future__ import annotations

import logging
from typing import Any

from app_config import KB_MILVUS_COLLECTION, KB_MILVUS_URI

logger = logging.getLogger("sonarcad.kb.vector")


class ChunkVectorStore:
    """pymilvus MilvusClient 薄封装。client 可注入（单测）。"""

    def __init__(self, uri: str | None = None, collection: str | None = None, client: Any = None):
        self._uri = uri or KB_MILVUS_URI
        self._collection = collection or KB_MILVUS_COLLECTION
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self._uri)
        return self._client

    def available(self) -> bool:
        try:
            self._get_client().list_collections()
            return True
        except Exception as exc:
            logger.warning("Milvus 不可达: %s", exc)
            return False

    def ensure_collection(self, dim: int) -> None:
        client = self._get_client()
        if client.has_collection(self._collection):
            return
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_key", DataType.VARCHAR, is_primary=True, max_length=96)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("location", DataType.VARCHAR, max_length=512)
        schema.add_field("text", DataType.VARCHAR, max_length=16384)
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(self._collection, schema=schema, index_params=index_params)
        logger.info("Milvus collection 已创建: %s (dim=%d)", self._collection, dim)

    def upsert_chunks(self, doc_id: str, chunks: list[dict[str, Any]],
                      vectors: list[list[float]]) -> int:
        """增量写入：先按 doc_id 删旧再插（§6 ⑥）。返回写入条数。"""
        self.ensure_collection(len(vectors[0]))
        client = self._get_client()
        client.delete(self._collection, filter=f'doc_id == "{doc_id}"')
        rows = [
            {
                "chunk_key": f"{doc_id}:{c['chunk_index']}",
                "vector": v,
                "doc_id": doc_id,
                "location": c["location"][:512],
                "text": c["text"][:16000],
            }
            for c, v in zip(chunks, vectors)
        ]
        client.insert(self._collection, rows)
        return len(rows)

    def delete_doc(self, doc_id: str) -> int:
        client = self._get_client()
        if not client.has_collection(self._collection):
            return 0
        # Milvus delete 返回删除统计（按实现），此处以查询计数为准
        hits = client.query(self._collection, filter=f'doc_id == "{doc_id}"',
                            output_fields=["chunk_key"])
        client.delete(self._collection, filter=f'doc_id == "{doc_id}"')
        return len(hits)

    def search(self, query_vector: list[float], top_k: int = 5,
               doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """top-k 检索。返回 [{chunk_key, doc_id, location, text, score}]。

        doc_ids 提供时按文档集合过滤（PHASE-006 库范围下推，D6-2）；空列表 = 不过滤（全量）。
        """
        client = self._get_client()
        if not client.has_collection(self._collection):
            return []
        expr = None
        if doc_ids:
            if len(doc_ids) > 1000:
                logger.warning("Milvus doc 集合过滤超 1000 项（%d），性能护栏提示", len(doc_ids))
            quoted = ", ".join(f'"{d}"' for d in doc_ids)
            expr = f"doc_id in [{quoted}]"
        results = client.search(
            self._collection, data=[query_vector], limit=top_k,
            filter=expr,
            output_fields=["doc_id", "location", "text", "chunk_key"],
        )
        out: list[dict[str, Any]] = []
        for hit in (results[0] if results else []):
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            out.append({
                "chunk_key": entity.get("chunk_key"),
                "doc_id": entity.get("doc_id"),
                "location": entity.get("location"),
                "text": entity.get("text"),
                "score": hit.get("distance"),
            })
        return out
