"""基础元数据存储（T13.7，表 meta_* 由 biz Flyway V19 建；§5.16 + 附录 D）。

四子项 CRUD：声呐类型 / 频率段 / 性能参数规范 / 提示词模板（版本化，同 (stage, name)
多版本仅一个启用——activate 时同键其余自动停用）。同步 psycopg 薄封装，与 log_store
同构；写端点由 api/metadata.py 收敛为 admin（管理面口径沿 kb.py，T11.2）。
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


def _rows(cur: Any) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]


# 可更新的普通列（白名单，防任意列写入）；id/created_at 不可改
_TABLE_COLUMNS: dict[str, list[str]] = {
    "sonar_types": ["install_form", "functional_system", "description", "enabled"],
    "freq_bands": ["name", "freq_min_hz", "freq_max_hz", "application", "reference_standard", "enabled"],
    "perf_specs": ["name", "unit", "value_min", "value_max", "definition", "enabled"],
    "prompt_templates": ["stage", "name", "template_text", "enabled", "remark"],
}

_TABLES = set(_TABLE_COLUMNS)


def list_rows(table: str, enabled_only: bool = False) -> list[dict[str, Any]]:
    if table not in _TABLES:
        raise ValueError(f"未知元数据表: {table}")
    order = {
        "sonar_types": "install_form, functional_system",
        "freq_bands": "freq_min_hz",
        "perf_specs": "name",
        "prompt_templates": "stage, name, version DESC",
    }[table]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM meta_{table} {'WHERE enabled' if enabled_only else ''} ORDER BY {order}")
        return _rows(cur)


def create_row(table: str, fields: dict[str, Any]) -> dict[str, Any]:
    if table not in _TABLES:
        raise ValueError(f"未知元数据表: {table}")
    cols = [c for c in _TABLE_COLUMNS[table] if c in fields]
    if not cols:
        raise ValueError("至少提供一个可写字段")
    if table == "prompt_templates":
        # 版本化：同 (stage, name) 自动取 max+1
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM meta_prompt_templates "
                "WHERE stage = %s AND name = %s",
                (fields.get("stage"), fields.get("name")))
            version = cur.fetchone()["v"]
        cols = [c for c in cols if c != "version"] + ["version"]
        fields = {**fields, "version": version}
    if table == "sonar_types" and ("install_form" not in fields or "functional_system" not in fields):
        raise ValueError("声呐类型需同时提供 install_form 与 functional_system（双维度枚举）")
    if table == "freq_bands" and not fields.get("name"):
        raise ValueError("频率段需提供 name")
    if table == "perf_specs" and not fields.get("name"):
        raise ValueError("性能参数规范需提供 name")
    if table == "prompt_templates" and not (fields.get("stage") and fields.get("name") and fields.get("template_text")):
        raise ValueError("提示词模板需提供 stage / name / template_text")
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO meta_{table} ({col_sql}) VALUES ({placeholders}) RETURNING *",
            [fields[c] for c in cols])
        row = cur.fetchone()
        conn.commit()
        return {k: _clean(v) for k, v in row.items()}


def update_row(table: str, row_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    if table not in _TABLES:
        raise ValueError(f"未知元数据表: {table}")
    cols = [c for c in _TABLE_COLUMNS[table] if c in fields]
    if not cols:
        raise ValueError("至少提供一个可写字段")
    sets = ", ".join(f"{c} = %s" for c in cols)
    params = [fields[c] for c in cols] + [row_id]
    with _connect() as conn, conn.cursor() as cur:
        # 提示词模板启用切换：同 (stage, name) 其余版本自动停用（多版本仅一个启用）
        if table == "prompt_templates" and fields.get("enabled") is True:
            cur.execute(
                "UPDATE meta_prompt_templates SET enabled = false "
                "WHERE stage = (SELECT stage FROM meta_prompt_templates WHERE id = %s) "
                "AND name = (SELECT name FROM meta_prompt_templates WHERE id = %s) AND id <> %s",
                (row_id, row_id, row_id))
        cur.execute(f"UPDATE meta_{table} SET {sets} WHERE id = %s RETURNING *", params)
        row = cur.fetchone()
        conn.commit()
        return {k: _clean(v) for k, v in row.items()} if row else None


def delete_row(table: str, row_id: int) -> bool:
    if table not in _TABLES:
        raise ValueError(f"未知元数据表: {table}")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM meta_{table} WHERE id = %s", (row_id,))
        conn.commit()
        return cur.rowcount > 0
