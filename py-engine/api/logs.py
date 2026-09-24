"""会话运行日志查询 API（总览页「全部日志」展开栏）。

worker 在 _publish_log 落库（V11 session_logs），此处只读回放。
遥测定位（A-12/A-16）：权威记录仍是 IR 快照与溯源链；功能上线前的历史会话
无留存，如实返回空列表。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from api.authz import require_user
from storage import log_store

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/sessions/{sid}")
def get_session_logs(sid: str, after_id: int = Query(default=0, ge=0),
                     limit: int = Query(default=5000, ge=1, le=20000),
                     user: str = Depends(require_user)) -> dict[str, Any]:
    """按会话回放日志（id 升序；after_id 供增量拉取）。"""
    return {"logs": log_store.list_logs(sid, after_id=after_id, limit=limit)}
