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


def append(session_id: str, level: str, text: str, kind: str = "log") -> None:
    """kind（T10.3，V13）：行类型——'log'=普通运行日志，'stage_duration'=阶段耗时结构化行
    （性能基线数据源；text 仍人类可读，机器解析走 kind 过滤）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO session_logs (session_id, level, text, kind) VALUES (%s, %s, %s, %s)",
                    (session_id, level, text, kind))
        conn.commit()


def list_logs(session_id: str, after_id: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, level, text, kind, created_at FROM session_logs "
            "WHERE session_id = %s AND id > %s ORDER BY id LIMIT %s",
            (session_id, after_id, limit),
        )
        return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]


def stage_durations(session_id: str) -> list[dict[str, Any]]:
    """按会话取阶段耗时行（T10.3 基线汇总用；text 中冗余了阶段名便于人工核对）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, text, created_at FROM session_logs "
            "WHERE session_id = %s AND kind = 'stage_duration' ORDER BY id",
            (session_id,),
        )
        return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]
