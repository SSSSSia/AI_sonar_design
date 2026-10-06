# -*- coding: utf-8 -*-
"""E1 正向参数更新存储（T12.2）：cad_param_updates 表（biz V16）。

传播深度计量：链深 = 会话内已落地的参数变更事件（forward 更新 + applied 反向提案）
的最大 depth，下一次变更为其 +1；>MAX_DEPTH 拒绝（系统实施方案 Max Depth=3）。
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

MAX_PROPAGATION_DEPTH = 3


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _clean(value: Any) -> Any:
    if isinstance(value, _uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: _clean(v) for k, v in row.items()} if row else None


def record(session_id: str, param_id: str, old_value: float | None, new_value: float,
           unit: str, reason: str | None, depth: int, ir2_version: int,
           created_by: str, origin: str = "user_forward") -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cad_param_updates
               (session_id, param_id, old_value, new_value, unit, reason, origin, depth,
                ir2_version, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (session_id, param_id, old_value, new_value, unit, reason, origin, depth,
             ir2_version, created_by),
        )
        row = cur.fetchone()
        conn.commit()
    return _clean_row(row)


def list_for_session(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM cad_param_updates WHERE session_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (session_id, limit),
        )
        return [_clean_row(r) for r in cur.fetchall()]


def current_depth(session_id: str) -> int:
    """会话内已落地参数变更链的当前深度（无变更 = 0；下一次变更 = 当前 + 1）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(depth), 0) AS depth FROM cad_param_updates WHERE session_id = %s",
                    (session_id,))
        row = cur.fetchone()
    return int(row["depth"])


def next_depth_or_raise(session_id: str) -> int:
    """下一次参数变更的深度；超 MAX_PROPAGATION_DEPTH 抛 ValueError（调用方转 409）。"""
    depth = current_depth(session_id) + 1
    if depth > MAX_PROPAGATION_DEPTH:
        raise ValueError(
            f"已达最大传播深度（{MAX_PROPAGATION_DEPTH}）：连锁参数变更过长，"
            "请走审阅关驳回返工或人工重新建模（Origin Lock 抑制语义）")
    return depth
