"""轻量信任头校验（T3.1；T11.2 收紧）。

gateway 已做 JWT 校验并注入 X-User-Id/X-User-Roles；py-engine 侧此前完全不校验入站头
（阶段 2 记录的收紧点）。KB 管理面端点要求至少携带 X-User-Id（缺失 401）。
T11.2（P2-5 三条硬规则）：①KB 写权限 = admin（kb.py 写端点 require_admin）；
②管理面（LLM 供应商等涉密钥端点）= admin；③**空角色集不再放行**——gateway 对
合法 JWT 恒注入 X-User-Roles（无角色用户为空串），直连绕过网关的不带角色头请求
一律 403（旧语义「角色头缺失即放行」是审查确认的越权漏洞）。
完整 JWT 校验仍由 gateway 承担（py-engine 内网直连场景以信任头为准）。
"""

from __future__ import annotations

from fastapi import Header, HTTPException


def _roles(x_user_roles: str | None) -> set[str]:
    return {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}


def require_user(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="缺少 X-User-Id（应经 gateway 注入）")
    return x_user_id


def require_writer(
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> str:
    require_user(x_user_id)
    roles = _roles(x_user_roles)
    if not (roles & {"admin", "designer"}):
        raise HTTPException(status_code=403, detail="该操作需要 admin/designer 角色")
    return x_user_id


def require_admin(
    x_user_id: str | None = Header(default=None),
    x_user_roles: str | None = Header(default=None),
) -> str:
    """管理面写操作（LLM 供应商/知识库写/系统管理，T11.2 统一收紧）仅 admin。"""
    require_user(x_user_id)
    roles = _roles(x_user_roles)
    if "admin" not in roles:
        raise HTTPException(status_code=403, detail="该操作需要 admin 角色")
    return x_user_id
