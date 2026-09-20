"""会话元数据直读（PHASE-006）：design_sessions.kb_ids（workflow 写、py-engine 同库只读）。

同 ir_store 直读模式：MQ 契约不变（a1 只带 session_id），kb_ids 由任务侧自查。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.session_meta")


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def get_kb_ids(session_id: str) -> Optional[list[str]]:
    """会话选定的知识库 id 列表；NULL/空/坏 JSON/非法 uuid = None（Agent 自主决策模式）。

    格式守卫：session_id 非法时直接 None（查库必炸），管线不因元数据异常中断。
    """
    if not session_id or not session_id.replace("-", "").isalnum():
        return None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT kb_ids FROM design_sessions WHERE id = %s", (session_id,))
            row = cur.fetchone()
    except Exception as exc:
        logger.warning("kb_ids 直读失败（按 Agent 模式处理）session=%s: %s", session_id, exc)
        return None
    if row is None or row["kb_ids"] is None:
        return None
    raw = row["kb_ids"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list):
        return None
    ids = [str(x) for x in raw if str(x).strip()]
    return ids or None
