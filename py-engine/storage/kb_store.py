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
# kb_bases 多库管理（PHASE-006，V9）：归属唯一事实源 = kb_base_documents
# ----------------------------------------------------------------------
def list_bases() -> list[dict[str, Any]]:
    """库列表 + 每库统计（引用计数口径：一份文档属多库时各库都计 1）。默认库置顶。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT b.id, b.name, b.description, b.is_default, b.created_by,
                      b.created_at, b.updated_at,
                      count(m.doc_id) AS documents,
                      count(m.doc_id) FILTER (WHERE d.status = 'indexed') AS indexed
               FROM kb_bases b
               LEFT JOIN kb_base_documents m ON m.base_id = b.id
               LEFT JOIN kb_documents d ON d.doc_id = m.doc_id
               GROUP BY b.id ORDER BY b.is_default DESC, b.created_at"""
        )
        return cur.fetchall()


def get_base(base_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM kb_bases WHERE id = %s", (base_id,))
        return cur.fetchone()


def create_base(name: str, description: Optional[str], is_default: bool = False,
                created_by: Optional[str] = None) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_bases (name, description, is_default, created_by)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (name, description, is_default, created_by),
        )
        return cur.fetchone()


def update_base(base_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
    allowed = {"name", "description"}
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        return get_base(base_id)
    sets = ", ".join(f"{k} = %s" for k in patch)
    values = list(patch.values()) + [base_id]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE kb_bases SET {sets}, updated_at = now() WHERE id = %s RETURNING *", values
        )
        return cur.fetchone()


def delete_base(base_id: str) -> tuple[bool, int]:
    """删库 = 解除全部归属引用后删库行（文档本体与索引数据不动，D6-4）。
    返回 (是否存在, 解除引用数)。默认库的保护在 API 层判断。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM kb_base_documents WHERE base_id = %s", (base_id,))
        refs = cur.fetchone()["n"]
        cur.execute("DELETE FROM kb_bases WHERE id = %s RETURNING id", (base_id,))
        deleted = cur.fetchone() is not None
    return deleted, refs


def add_docs_to_base(base_id: str, doc_ids: list[str], added_by: Optional[str] = None) -> int:
    """批量加入归属（幂等：已存在的引用跳过）。返回新增引用数。"""
    n = 0
    with _connect() as conn, conn.cursor() as cur:
        for doc_id in doc_ids:
            cur.execute(
                """INSERT INTO kb_base_documents (base_id, doc_id, added_by)
                   SELECT %s, %s, %s WHERE NOT EXISTS (
                       SELECT 1 FROM kb_base_documents WHERE base_id = %s AND doc_id = %s
                   )""",
                (base_id, doc_id, added_by, base_id, doc_id),
            )
            n += cur.rowcount
    return n


def remove_docs_from_base(base_id: str, doc_ids: list[str]) -> int:
    """批量移出归属（仅解除本库引用，文档多归属时其他库不受影响）。返回移除数。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM kb_base_documents WHERE base_id = %s AND doc_id = ANY(%s)",
            (base_id, doc_ids),
        )
        return cur.rowcount


def doc_base_ids(doc_id: str) -> list[str]:
    """某文档当前归属的全部库 id（多归属）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT base_id FROM kb_base_documents WHERE doc_id = %s ORDER BY added_at", (doc_id,)
        )
        return [r["base_id"] for r in cur.fetchall()]


def list_documents(limit: int = 100, base_id: Optional[str] = None,
                   unassigned: bool = False,
                   doc_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """文档列表。base_id=按归属过滤；unassigned=True 只看无归属（未归库）；
    doc_ids=按文档集合过滤（kb_scope 用，仅内部）；行带 bases（归属标签列表）。"""
    sql = """SELECT d.* FROM kb_documents d"""
    where: list[str] = []
    params: list[Any] = []
    if base_id is not None:
        sql += " JOIN kb_base_documents m ON m.doc_id = d.doc_id AND m.base_id = %s"
        params.append(base_id)
    if unassigned:
        where.append("NOT EXISTS (SELECT 1 FROM kb_base_documents x WHERE x.doc_id = d.doc_id)")
    if doc_ids is not None:
        if not doc_ids:
            return []
        where.append("d.doc_id = ANY(%s)")
        params.append(doc_ids)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.created_at DESC LIMIT %s"
    params.append(limit)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    tags: dict[str, list[dict[str, Any]]] = {}
    for r in _doc_base_tags([r["doc_id"] for r in rows]):
        tags.setdefault(r["doc_id"], []).append({"base_id": r["base_id"], "base_name": r["base_name"]})
    for r in rows:
        r["bases"] = tags.get(r["doc_id"], [])
    return rows


def _doc_base_tags(doc_ids: list[str]) -> list[dict[str, Any]]:
    """批量取文档的归属标签（doc_id, base_id, base_name）。"""
    if not doc_ids:
        return []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.doc_id, m.base_id, b.name AS base_name, b.is_default
               FROM kb_base_documents m JOIN kb_bases b ON b.id = m.base_id
               WHERE m.doc_id = ANY(%s) ORDER BY m.doc_id, b.is_default DESC, b.name""",
            (doc_ids,),
        )
        return cur.fetchall()


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
