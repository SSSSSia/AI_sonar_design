"""独立审计日志落库（表 audit_logs，biz Flyway V17 建表；T13.1 §9.4）。

写入方有二：Java 各服务 AuditRecorder（登录/用户/项目/会话/裁决）与本模块
（api/audit.py 审计中间件：KB 写端点 + 导出端点）。查询端点在 auth 服务
（GET /api/v1/audit/logs，admin）。审计是旁路：调用方负责 try/except，
落库失败不影响业务响应。同步 psycopg 薄封装，出口规范化与 log_store 同构。
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app_config import DATABASE_URL


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL.replace("+psycopg", ""), row_factory=dict_row)


def _clean(value: Any) -> Any:
    if isinstance(value, uuid_mod.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def append(
    user_id: str | None,
    username: str | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """落一行审计（§9.4：记录「由谁、在何时、对什么资源、做了什么」）。

    detail 只放查询参数摘要等非敏感信息（宪法 §10：不落密钥/凭据明文——
    py 侧中间件刻意不落请求体，llm-providers 等端点 body 含密钥）。
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_logs (user_id, username, action, resource_type, resource_id, detail, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                user_id,
                username,
                action,
                resource_type,
                resource_id,
                Json(detail) if detail is not None else None,
                ip,
            ),
        )
        conn.commit()


def list_logs(
    username: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    hours: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """审计检索（admin 用，与 auth 服务查询端点同语义；py 侧供单测/调试复用）。"""
    conds = []
    params: list[Any] = []
    if username:
        conds.append("COALESCE(a.username, u.username) ILIKE %s")
        params.append(f"%{username}%")
    if action:
        conds.append("a.action ILIKE %s")
        params.append(f"{action}%")
    if resource_type:
        conds.append("a.resource_type = %s")
        params.append(resource_type)
    if hours:
        conds.append("a.created_at >= now() - (%s || ' hours')::interval")
        params.append(str(hours))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total FROM audit_logs a "
            "LEFT JOIN users u ON u.id::text = a.user_id " + where,
            params,
        )
        total = cur.fetchone()["total"]
        cur.execute(
            "SELECT a.id, a.user_id, COALESCE(a.username, u.username) AS username, a.action, "
            "a.resource_type, a.resource_id, a.detail, a.ip, a.created_at "
            "FROM audit_logs a LEFT JOIN users u ON u.id::text = a.user_id "
            + where
            + " ORDER BY a.id DESC LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        rows = [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]
    return {"total": total, "logs": rows}
