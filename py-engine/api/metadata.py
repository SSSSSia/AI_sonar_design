"""基础元数据管理 API（T13.7，§5.16 + 附录 D；gateway /api/v1/metadata/** 直转 py-engine）。

路由前缀 /metadata。四子项 CRUD：
- GET  /metadata/{table}?enabled_only=   列表（登录即可读——元数据是设计支撑数据）
- POST /metadata/{table}                 新建（admin；提示词模板版本自动递增）
- PATCH /metadata/{table}/{row_id}       更新（admin；提示词模板启用切换=同键互斥）
- DELETE /metadata/{table}/{row_id}      删除（admin）
table ∈ sonar_types | freq_bands | perf_specs | prompt_templates。
LLM 模型注册表（§5.16 第五子项）已由 /kb/llm-providers 承担（v0.1 落地），此处不重复。
写端点审计：随 T13.1 中间件规则表（metadata.* 动作）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.authz import require_admin, require_user
from storage import metadata_store

router = APIRouter(prefix="/metadata", dependencies=[Depends(require_user)])

_TABLES = {"sonar_types", "freq_bands", "perf_specs", "prompt_templates"}


def _table(table: str) -> str:
    if table not in _TABLES:
        raise HTTPException(status_code=404, detail=f"未知元数据类别: {table}（可选: {sorted(_TABLES)}）")
    return table


@router.get("/{table}")
def list_rows(table: str, enabled_only: bool = False) -> dict[str, Any]:
    try:
        return {"rows": metadata_store.list_rows(_table(table), enabled_only=enabled_only)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class PatchIn(BaseModel):
    fields: dict[str, Any]


@router.post("/{table}", status_code=201)
def create_row(table: str, body: dict[str, Any], user: str = Depends(require_admin)) -> dict[str, Any]:
    t = _table(table)
    try:
        return {"row": metadata_store.create_row(t, body)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 唯一冲突等 DB 错误如实透出
        raise HTTPException(status_code=409, detail=f"写入失败（可能违反唯一约束）: {exc}") from exc


@router.patch("/{table}/{row_id}")
def update_row(table: str, row_id: int, body: PatchIn, user: str = Depends(require_admin)) -> dict[str, Any]:
    t = _table(table)
    try:
        row = metadata_store.update_row(t, row_id, body.fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"{table} #{row_id} 不存在")
    return {"row": row}


@router.delete("/{table}/{row_id}")
def delete_row(table: str, row_id: int, user: str = Depends(require_admin)) -> dict[str, Any]:
    t = _table(table)
    if not metadata_store.delete_row(t, row_id):
        raise HTTPException(status_code=404, detail=f"{table} #{row_id} 不存在")
    return {"id": row_id, "deleted": True}
