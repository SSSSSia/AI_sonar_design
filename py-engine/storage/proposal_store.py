# -*- coding: utf-8 -*-
"""E1 参数变更提案存储（T11.3）：cad_tune_param_proposals 表（biz V15）。

模式与 tune_store 一致：psycopg 直连、dict_row、出口 _clean 规范化。
( tune_id, round_no, param_id ) 唯一——同轮重生成提案用 replace_round 整组替换。
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app_config import DATABASE_URL

_PROPOSAL_STATUS = {"proposed", "applied", "ignored"}


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _clean(value: Any) -> Any:
    if isinstance(value, __import__("uuid").UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: _clean(v) for k, v in row.items()} if row else None


def replace_round(session_id: str, tune_id: str, round_no: int, ir3_version: int,
                  baseline_ir_version: int, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整组替换某轮提案（重复 finalize/重生成幂等）；无提案则清空该轮旧提案。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cad_tune_param_proposals WHERE tune_id = %s AND round_no = %s "
            "AND status = 'proposed'",
            (tune_id, round_no),
        )
        rows: list[dict[str, Any]] = []
        for p in proposals:
            cur.execute(
                """INSERT INTO cad_tune_param_proposals
                   (session_id, tune_id, round_no, ir3_version, baseline_ir_version,
                    param_id, param_name, unit, old_value, new_value, delta_pct, dimension, note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (session_id, tune_id, round_no, ir3_version, baseline_ir_version,
                 p["param_id"], p.get("param_name"), p.get("unit") or "mm",
                 p["old_value"], p["new_value"], p["delta_pct"], p["dimension"], p.get("note")),
            )
            rows.append(cur.fetchone())
        conn.commit()
    return [_clean_row(r) for r in rows if r]


def list_for_session(session_id: str, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM cad_tune_param_proposals WHERE session_id = %s"
    args: list[Any] = [session_id]
    if status:
        if status not in _PROPOSAL_STATUS:
            raise ValueError(f"非法提案状态: {status}")
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY created_at DESC, param_id"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(args))
        return [_clean_row(r) for r in cur.fetchall()]


def get(proposal_id: str) -> dict[str, Any] | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM cad_tune_param_proposals WHERE id = %s", (proposal_id,))
        return _clean_row(cur.fetchone())


def mark(proposal_ids: list[str], status: str, decided_by: str,
         applied_ir2_version: int | None = None) -> int:
    """置状态（proposed → applied/ignored）。只允许从 proposed 迁移（applied 不可回退）。"""
    if status not in {"applied", "ignored"}:
        raise ValueError(f"非法目标状态: {status}")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE cad_tune_param_proposals
               SET status = %s, decided_at = now(), decided_by = %s,
                   applied_ir2_version = COALESCE(%s, applied_ir2_version)
               WHERE id = ANY(%s) AND status = 'proposed'""",
            (status, decided_by, applied_ir2_version, proposal_ids),
        )
        n = cur.rowcount
        conn.commit()
    return n


def rearm(session_id: str, applied_ir2_version: int) -> int:
    """回写后重派失败的自愈入口：把已 applied 的提案退回 proposed（仅当其 applied 版本
    就是本次写入的版本，避免误伤历史轮次）。"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE cad_tune_param_proposals
               SET status = 'proposed', decided_at = NULL, decided_by = NULL, applied_ir2_version = NULL
               WHERE session_id = %s AND status = 'applied' AND applied_ir2_version = %s""",
            (session_id, applied_ir2_version),
        )
        n = cur.rowcount
        conn.commit()
    return n
