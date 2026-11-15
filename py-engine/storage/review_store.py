"""review_records 只读访问（T15.6/C-1，ADR-014 §5）。

py-engine 对裁决表**只读**：正式导出按批准组合（review_records.metadata.snapshot_combo，
V20 jsonb 列）读取快照并校验，不得读"当时 latest"（DEV-001 §3 C-1）。写入侧在 Java
workflow 服务（ReviewService 裁决事务属主），此处不做任何写操作（职责单一）。

连接参数来自 app_config.DATABASE_URL（.env），与 ir_store 同一底座。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import psycopg

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.review_store")


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""))


def _as_obj(value: Any) -> Any:
    """jsonb 列 psycopg3 默认已反序列化为 dict/list；历史行/异常值兜底原样返回。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def get_review_record(session_id: str, record_id: str) -> Optional[dict[str, Any]]:
    """按 (session, record_id) 取裁决记录（metadata jsonb 已解析）；不存在返回 None。

    只读、不过滤任何状态——记录本身是不可变裁决事实（组合有效性由调用方按政策校验）。
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id::text, gate, decision, ir_snapshot_id::text, annotations, metadata, decided_at
               FROM review_records WHERE session_id = %s AND id = %s""",
            (session_id, record_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "gate": row[1],
        "decision": row[2],
        "ir_snapshot_id": row[3],
        "annotations": _as_obj(row[4]),
        "metadata": _as_obj(row[5]),
        "decided_at": row[6].isoformat() if row[6] is not None else None,
    }
