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


def search_logs(session_id: str | None = None, level: str | None = None,
                kind: str | None = None, keyword: str | None = None,
                hours: int | None = None,
                limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """跨会话日志检索（T12.5，AdminPage 日志中心）。全部过滤可选；
    session_id 支持前缀匹配；keyword 走 text ILIKE；hours=近 N 小时窗。"""
    conds: list[str] = []
    args: list[Any] = []
    if session_id:
        conds.append("session_id::text LIKE %s")
        args.append(f"{session_id.strip()}%")
    if level:
        conds.append("level = %s")
        args.append(level)
    if kind:
        conds.append("kind = %s")
        args.append(kind)
    if keyword:
        conds.append("text ILIKE %s")
        args.append(f"%{keyword.strip()}%")
    if hours:
        conds.append("created_at > now() - (%s || ' hours')::interval")
        args.append(str(int(hours)))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM session_logs {where}", tuple(args))
        total = int(cur.fetchone()["total"])
        cur.execute(
            f"SELECT id, session_id, level, text, kind, created_at FROM session_logs {where} "
            "ORDER BY id DESC LIMIT %s OFFSET %s",
            tuple(args + [limit, offset]),
        )
        rows = [{k: _clean(v) for k, v in r.items()} for r in cur.fetchall()]
    return {"logs": rows, "total": total, "limit": limit, "offset": offset}
