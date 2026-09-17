"""IR 快照存储（T1.3，A-12）。

worker 与共享存储（PG ir_snapshots 表，V1 建表）的薄封装：
- 读最新快照（A1 读 IR0）
- 追加不可变快照（version 单调递增，UPDATE 语义不存在）
- 会话 current_stage 维护（编排层职责经 REST 回调，算力侧只更新阶段标记）

连接参数来自 app_config.DATABASE_URL（.env）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import psycopg

from app_config import DATABASE_URL
from ir_contract import SCHEMA_VERSION

logger = logging.getLogger("sonarcad.ir_store")


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""))


def get_latest_snapshot(session_id: str, ir_type: str) -> Optional[dict[str, Any]]:
    """取某类型最新版本快照内容；不存在返回 None。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT content FROM ir_snapshots
               WHERE session_id = %s AND ir_type = %s
               ORDER BY version DESC LIMIT 1""",
            (session_id, ir_type),
        )
        row = cur.fetchone()
    return row[0] if row else None


def save_snapshot(session_id: str, ir_type: str, content: dict[str, Any], created_by: str) -> int:
    """追加不可变快照，返回版本号（同 (session, type) 内 max+1）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) FROM ir_snapshots WHERE session_id = %s AND ir_type = %s",
            (session_id, ir_type),
        )
        version = cur.fetchone()[0] + 1
        cur.execute(
            """INSERT INTO ir_snapshots (session_id, ir_type, version, schema_version, content, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (session_id, ir_type, version, SCHEMA_VERSION, json.dumps(content, ensure_ascii=False), created_by),
        )
    logger.info("IR 快照已追加 session=%s %s v%s by=%s", session_id, ir_type, version, created_by)
    return version


def update_stage(session_id: str, stage: str) -> None:
    """更新会话当前阶段标记（A1..A5/gate）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE design_sessions SET current_stage = %s, updated_at = now() WHERE id = %s",
            (stage, session_id),
        )
