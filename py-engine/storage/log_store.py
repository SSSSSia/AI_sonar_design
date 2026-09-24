"""会话运行日志留存（表 session_logs，V11 建表）。

worker 关键步在 WS 实时推送之外顺带落库（_publish_log 调用），供总览页
「全部日志」回看。定位（A-12/A-16）：运行遥测，权威记录仍是 IR 快照与溯源链。
同步 psycopg 薄封装，出口规范化与 tune_store 同构。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _clean(value: Any) -> Any:
    if isinstance(value, uuid_mod.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def append(session_id: str, level: str, text: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO session_logs (session_id, level, text) VALUES (%s, %s, %s)",
                    (session_id, level, text))
        conn.commit()


def list_logs(session_id: str, after_id: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, level, text, created_at FROM session_logs "
            "WHERE session_id = %s AND id > %s ORDER BY id LIMIT %s",
            (session_id, after_id, limit),
        )
        return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]
