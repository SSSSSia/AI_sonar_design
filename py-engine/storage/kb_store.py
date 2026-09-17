"""知识文档管线存储（T3.1，表 kb_documents / kb_chunks / kb_extractions，V6 建表）。

同步 psycopg 薄封装，dict_row 返回。kb_documents.status 状态机：
uploaded → parsed → chunked → indexed（异常分支 failed，error 如实记录）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.kb_store")


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def new_doc_id() -> str:
    return uuid.uuid4().hex[:16]


# ----------------------------------------------------------------------
# kb_documents
# ----------------------------------------------------------------------
def create_document(filename: str, fmt: str, size_bytes: int, bucket: str,
                    object_key: str, uploaded_by: str) -> dict[str, Any]:
    doc_id = new_doc_id()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_documents
               (doc_id, filename, format, size_bytes, bucket, object_key, status, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s) RETURNING *""",
            (doc_id, filename, fmt, size_bytes, bucket, object_key, uploaded_by),
        )
        row = cur.fetchone()
    logger.info("KB 文档建档 doc_id=%s file=%s fmt=%s", doc_id, filename, fmt)
    return row


def get_document(doc_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM kb_documents WHERE doc_id = %s", (doc_id,))
        return cur.fetchone()


def list_documents(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM kb_documents ORDER BY created_at DESC LIMIT %s", (limit,)
        )
        return cur.fetchall()


def update_document(doc_id: str, **fields: Any) -> None:
    """白名单字段更新（status/parsed_units/page_count/section_count/ocr_pending/error）。"""
    allowed = {"status", "parsed_units", "page_count", "section_count", "ocr_pending", "error"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return
    if "parsed_units" in patch and isinstance(patch["parsed_units"], (list, dict)):
        patch["parsed_units"] = json.dumps(patch["parsed_units"], ensure_ascii=False)
    sets = ", ".join(f"{k} = %s" for k in patch)
    values = list(patch.values()) + [doc_id]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE kb_documents SET {sets}, updated_at = now() WHERE doc_id = %s", values
        )


def delete_document(doc_id: str) -> bool:
    """删除档案（chunks/extractions 由 ON DELETE CASCADE 级联）。返回是否存在。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kb_documents WHERE doc_id = %s RETURNING doc_id", (doc_id,))
        return cur.fetchone() is not None


def doc_stats() -> dict[str, Any]:
    """知识库现状诚实统计（§6 硬性要求：实体/文档数量可见）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM kb_documents")
        docs = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM kb_chunks")
        chunks = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM kb_extractions WHERE status = 'confirmed'")
        confirmed = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM kb_extractions WHERE status = 'pending'")
        pending = cur.fetchone()["n"]
    return {"documents": docs, "chunks": chunks,
            "extractions_confirmed": confirmed, "extractions_pending": pending}


# ----------------------------------------------------------------------
# kb_chunks
# ----------------------------------------------------------------------
def replace_chunks(doc_id: str, chunks: list[dict[str, Any]]) -> int:
    """整批替换某文档的 chunks（增量重建语义：先删旧再插）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kb_chunks WHERE doc_id = %s", (doc_id,))
        for i, c in enumerate(chunks, 1):
            cur.execute(
                """INSERT INTO kb_chunks (doc_id, chunk_index, location, text)
                   VALUES (%s, %s, %s, %s)""",
                (doc_id, c.get("chunk_index", i), c["location"], c["text"]),
            )
    logger.info("KB chunks 已替换 doc_id=%s n=%d", doc_id, len(chunks))
    return len(chunks)


def get_chunks(doc_id: str) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_index, location, text, embedded FROM kb_chunks "
            "WHERE doc_id = %s ORDER BY chunk_index",
            (doc_id,),
        )
        return cur.fetchall()


def all_indexed_chunks(limit: int = 5000) -> list[dict[str, Any]]:
    """检索文本级动态语料：全部 indexed 文档的 chunks。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.doc_id, c.chunk_index, c.location, c.text
               FROM kb_chunks c JOIN kb_documents d ON d.doc_id = c.doc_id
               WHERE d.status = 'indexed' ORDER BY c.doc_id, c.chunk_index LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def mark_embedded(doc_id: str, embedded: bool) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE kb_chunks SET embedded = %s WHERE doc_id = %s", (embedded, doc_id))


# ----------------------------------------------------------------------
# kb_extractions
# ----------------------------------------------------------------------
def replace_extractions(doc_id: str, items: list[dict[str, Any]]) -> int:
    """整批替换某文档的抽取结果（状态清回 pending——新抽取需重新人工确认）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM kb_extractions WHERE doc_id = %s", (doc_id,))
        for item in items:
            cur.execute(
                """INSERT INTO kb_extractions
                   (doc_id, chunk_index, kind, etype, name, payload, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending')""",
                (doc_id, item.get("chunk_index"), item["kind"], item["etype"],
                 item["name"], json.dumps(item.get("payload", {}), ensure_ascii=False)),
            )
    return len(items)


def get_extractions(doc_id: str, status: Optional[str] = None) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        if status:
            cur.execute(
                "SELECT * FROM kb_extractions WHERE doc_id = %s AND status = %s ORDER BY id",
                (doc_id, status),
            )
        else:
            cur.execute(
                "SELECT * FROM kb_extractions WHERE doc_id = %s ORDER BY id", (doc_id,)
            )
        return cur.fetchall()


def set_extraction_status(extraction_id: int, status: str) -> Optional[dict[str, Any]]:
    """人工裁决（§6 ④）：confirmed / rejected。返回更新后的行。"""
    if status not in ("confirmed", "rejected", "pending"):
        raise ValueError(f"非法抽取状态: {status}")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE kb_extractions SET status = %s WHERE id = %s RETURNING *",
            (status, extraction_id),
        )
        return cur.fetchone()


def get_confirmed_extractions(doc_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        if doc_id:
            cur.execute(
                "SELECT * FROM kb_extractions WHERE status = 'confirmed' AND doc_id = %s ORDER BY id",
                (doc_id,),
            )
        else:
            cur.execute(
                "SELECT * FROM kb_extractions WHERE status = 'confirmed' ORDER BY id"
            )
        return cur.fetchall()


def set_extraction_graph_key(extraction_id: int, graph_key: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE kb_extractions SET graph_key = %s WHERE id = %s",
            (graph_key, extraction_id),
        )
