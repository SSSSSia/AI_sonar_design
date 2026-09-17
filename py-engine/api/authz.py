"""轻量信任头校验（T3.1）。

gateway 已做 JWT 校验并注入 X-User-Id/X-User-Roles；py-engine 侧此前完全不校验入站头
（阶段 2 记录的收紧点）。KB 管理面端点要求至少携带 X-User-Id（缺失 401），
写操作另要求角色头含 admin/designer。完整 JWT 落地随阶段 4 系统管理收紧——如实记录。
"""

from __future__ import annotations

from fastapi import Header, HTTPException


def require_user(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="缺少 X-User-Id（应经 gateway 注入）")
    return x_user_id


def require_writer(
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> str:
    require_user(x_user_id)
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if roles and not (roles & {"admin", "designer"}):
        raise HTTPException(status_code=403, detail="KB 写操作需要 admin/designer 角色")
    return x_user_id
