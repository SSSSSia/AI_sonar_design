"""CAD 交互微调存储（T8.1，PHASE-008，表 cad_tune_sessions / _rounds / _tool_calls，V10 建表）。

同步 psycopg 薄封装，dict_row 返回（与 kb_store 同构）。uuid/datetime 列在出口统一转
str/isoformat——psycopg 返回 UUID/datetime 对象，直进契约会炸 jsonschema（T6.7 e2e 教训）。

状态机：
- cad_tune_sessions.status: active → finalized | abandoned
- cad_tune_rounds.status:   running → awaiting_upload → done | failed
- cad_tune_tool_calls.status: pending → done | timeout（服务端等待超时；迟到的回传拒收）
"""

from __future__ import annotations

import json
import logging
import time
import uuid as uuid_mod
from datetime import date, datetime
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

logger = logging.getLogger("sonarcad.tune_store")


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _clean(value: Any) -> Any:
    """出口规范化：UUID→str、date/datetime→isoformat（psycopg 原生对象不可直进 JSON 契约）。"""
    if isinstance(value, uuid_mod.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _clean_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {k: _clean(v) for k, v in row.items()}


def _dump(value: Any) -> Any:
    """jsonb 入参：dict/list → JSON 串；None/标量原样。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


# ----------------------------------------------------------------------
# cad_tune_sessions
# ----------------------------------------------------------------------
def create_tune_session(design_session_id: str, baseline_ir_version: int,
                        created_by: str, note: str | None = None) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cad_tune_sessions
               (design_session_id, baseline_ir_version, created_by, note)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (design_session_id, baseline_ir_version, created_by, note),
        )
        row = cur.fetchone()
    logger.info("微调会话已创建 tune=%s design=%s baseline=v%s", row["id"], design_session_id, baseline_ir_version)
    return _clean_row(row)


def get_tune_session(tune_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cad_tune_sessions WHERE id = %s", (tune_id,))
        return _clean_row(cur.fetchone())


def get_active_tune_session(design_session_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM cad_tune_sessions
               WHERE design_session_id = %s AND status = 'active'
               ORDER BY created_at DESC LIMIT 1""",
            (design_session_id,),
        )
        return _clean_row(cur.fetchone())


def list_tune_sessions(design_session_id: str) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM cad_tune_sessions WHERE design_session_id = %s
               ORDER BY created_at DESC""",
            (design_session_id,),
        )
        return [_clean_row(r) for r in cur.fetchall()]


def update_tune_status(tune_id: str, status: str, note: str | None = None) -> Optional[dict[str, Any]]:
    """状态推进（active→finalized/abandoned）；note 可选覆盖。目标不存在返回 None。"""
    allowed = {"active", "finalized", "abandoned"}
    if status not in allowed:
        raise ValueError(f"非法微调会话状态：{status}（允许 {'|'.join(sorted(allowed))}）")
    with _connect() as conn, conn.cursor() as cur:
        if note is not None:
            cur.execute(
                """UPDATE cad_tune_sessions
                   SET status = %s, note = %s, updated_at = now()
                   WHERE id = %s RETURNING *""",
                (status, note, tune_id),
            )
        else:
            cur.execute(
                "UPDATE cad_tune_sessions SET status = %s, updated_at = now() WHERE id = %s RETURNING *",
                (status, tune_id),
            )
        row = cur.fetchone()
    return _clean_row(row)


# ----------------------------------------------------------------------
# cad_tune_rounds
# ----------------------------------------------------------------------
def latest_round_no(tune_id: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(round_no), 0) AS max_no FROM cad_tune_rounds WHERE tune_session_id = %s", (tune_id,))
        return cur.fetchone()["max_no"]


def create_round(tune_id: str, round_no: int, baseline_ir_version: int,
                 prompt: str, selection: dict[str, Any] | None, created_by: str) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cad_tune_rounds
               (tune_session_id, round_no, baseline_ir_version, prompt, selection, created_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (tune_id, round_no, baseline_ir_version, prompt, _dump(selection), created_by),
        )
        row = cur.fetchone()
    logger.info("微调轮次已创建 tune=%s round=%s baseline=v%s", tune_id, round_no, baseline_ir_version)
    return _clean_row(row)


def get_round(tune_id: str, round_no: int) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM cad_tune_rounds WHERE tune_session_id = %s AND round_no = %s",
            (tune_id, round_no),
        )
        return _clean_row(cur.fetchone())


def list_rounds(tune_id: str) -> list[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM cad_tune_rounds WHERE tune_session_id = %s ORDER BY round_no",
            (tune_id,),
        )
        return [_clean_row(r) for r in cur.fetchall()]


_ROUND_FIELDS = {"status", "llm_trace", "script", "result_ir3_version", "error"}


def update_round(round_id: str, **fields: Any) -> None:
    """白名单字段更新（status/llm_trace/script/result_ir3_version/error）。"""
    patch = {k: v for k, v in fields.items() if k in _ROUND_FIELDS}
    if not patch:
        return
    if "llm_trace" in patch and isinstance(patch["llm_trace"], (list, dict)):
        patch["llm_trace"] = json.dumps(patch["llm_trace"], ensure_ascii=False)
    sets = ", ".join(f"{k} = %s" for k in patch)
    values = list(patch.values()) + [round_id]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE cad_tune_rounds SET {sets} WHERE id = %s", values)


def expire_running_rounds() -> int:
    """启动自愈：服务重启时 running/awaiting_upload 轮次如实标 failed（A-16），挂起工具调用标 timeout。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE cad_tune_rounds SET status = 'failed',
                   error = '服务重启中断，本轮未完成——请重新发起微调'
               WHERE status IN ('running', 'awaiting_upload') RETURNING id"""
        )
        rows = cur.fetchall()
        if rows:
            cur.execute(
                """UPDATE cad_tune_tool_calls SET status = 'timeout',
                       result = '{"error": "服务重启中断"}'::jsonb, completed_at = now()
                   WHERE status = 'pending'
                     AND round_id IN (SELECT id FROM cad_tune_rounds
                                      WHERE tune_session_id IN
                                        (SELECT tune_session_id FROM cad_tune_rounds WHERE id = ANY(%s)))""",
                ([r["id"] for r in rows],),
            )
    if rows:
        logger.warning("启动自愈：%s 个中断轮次已标 failed", len(rows))
    return len(rows)


# ----------------------------------------------------------------------
# cad_tune_tool_calls
# ----------------------------------------------------------------------
def create_tool_call(round_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cad_tune_tool_calls (round_id, name, args)
               VALUES (%s, %s, %s) RETURNING *""",
            (round_id, name, _dump(args)),
        )
        row = cur.fetchone()
    return _clean_row(row)


def get_tool_call(call_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cad_tune_tool_calls WHERE id = %s", (call_id,))
        return _clean_row(cur.fetchone())


def poll_tool_calls(tune_id: str, after_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """取该微调会话 cursor 之后仍 pending 的工具调用（插件长轮询的即时查询）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT tc.id AS call_id, tc.name, tc.args, tc.status, tc.created_at,
                      r.round_no
               FROM cad_tune_tool_calls tc
               JOIN cad_tune_rounds r ON r.id = tc.round_id
               WHERE r.tune_session_id = %s AND tc.status = 'pending' AND tc.id > %s
               ORDER BY tc.id LIMIT %s""",
            (tune_id, after_id, limit),
        )
        return [_clean_row(r) for r in cur.fetchall()]


def complete_tool_call(call_id: int, result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """插件回传结果；仅 pending 可完成（timeout 后迟到的回传拒收，返回 None——如实拒绝）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE cad_tune_tool_calls
               SET result = %s, status = 'done', completed_at = now()
               WHERE id = %s AND status = 'pending' RETURNING *""",
            (_dump(result), call_id),
        )
        row = cur.fetchone()
    return _clean_row(row)


def timeout_tool_call(call_id: int, reason: str) -> dict[str, Any]:
    """服务端等待超时：标 timeout + 合成错误结果（AgentLoop 据此注入修复提示）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE cad_tune_tool_calls
               SET status = 'timeout', result = %s, completed_at = now()
               WHERE id = %s RETURNING *""",
            (_dump({"ok": False, "error": reason}), call_id),
        )
        row = cur.fetchone()
    logger.warning("工具调用等待超时 call=%s", call_id)
    return _clean_row(row)


def wait_tool_call(call_id: int, timeout_s: float,
                   poll_interval_s: float = 0.5) -> dict[str, Any]:
    """服务端阻塞等待插件回传（AgentLoop 工具执行器内部用）。超时标 timeout 并返回合成结果。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = get_tool_call(call_id)
        if row and row["status"] != "pending":
            return row
        time.sleep(poll_interval_s)
    row = get_tool_call(call_id)
    if row and row["status"] == "pending":
        row = timeout_tool_call(call_id, f"插件执行等待超时（{int(timeout_s)}s）——请在 FreeCAD 端查看是否卡死")
    return row or {"id": call_id, "status": "timeout",
                   "result": {"ok": False, "error": f"工具调用 {call_id} 不存在"}}
