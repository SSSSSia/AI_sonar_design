# -*- coding: utf-8 -*-
"""T11.2 单测：细粒度 RBAC 三条硬规则（py-engine 侧）。

①KB 写权限 = admin（kb.py 13 个写端点 require_writer→require_admin）
②管理面 = admin（/llm/provider/reload 等）
③authz 空角色集不再放行（审查确认的越权漏洞：角色头缺失/为空一律 403）
读端点保持已登录即可（require_user）。
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}
DESIGNER = {"X-User-Id": "d", "X-User-Roles": "designer"}
VIEWER = {"X-User-Id": "v", "X-User-Roles": "viewer"}
NO_ROLES = {"X-User-Id": "n"}  # 直连绕过网关、不带角色头——旧语义放行，T11.2 起 403


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


# ----------------------------------------------------------------------
# 硬规则③：空角色集不再放行
# ----------------------------------------------------------------------
def test_missing_roles_header_is_403_on_write(client, monkeypatch):
    import storage.registry_store as rs

    monkeypatch.setattr(rs, "create_row", lambda *a, **k: pytest.fail("不应触达存储层"))
    r = client.post("/api/v1/kb/registry/standards", headers=NO_ROLES, json={"fields": {"code": "X"}})
    assert r.status_code == 403


def test_missing_roles_header_is_403_on_admin_endpoint(client):
    r = client.post("/llm/provider/reload", headers=NO_ROLES)
    assert r.status_code == 403


def test_unit_level_authz_semantics():
    from fastapi import HTTPException

    from api.authz import require_admin, require_user, require_writer

    assert require_user("u") == "u"
    assert require_writer("u", "designer") == "u"
    assert require_admin("u", "admin,designer") == "u"  # 逗号分隔解析
    for fn in (require_writer, require_admin):
        with pytest.raises(HTTPException) as ei:
            fn("u", None)  # 旧版此处放行（漏洞）
        assert ei.value.status_code == 403
        with pytest.raises(HTTPException) as ei2:
            fn("u", "")
        assert ei2.value.status_code == 403
    with pytest.raises(HTTPException) as e3:
        require_admin("u", "designer")
    assert e3.value.status_code == 403


# ----------------------------------------------------------------------
# 硬规则②：KB 写权限 = admin（designer 不再可写知识库）
# ----------------------------------------------------------------------
def test_kb_registry_write_requires_admin(client, monkeypatch):
    import storage.registry_store as rs

    created = []
    monkeypatch.setattr(rs, "create_row",
                        lambda kind, fields: created.append((kind, fields)) or {"id": 1, **fields})
    r = client.post("/api/v1/kb/registry/standards", headers=DESIGNER,
                    json={"fields": {"code": "D-1", "name": "x", "applies_to": "y", "confidence": "low"}})
    assert r.status_code == 403
    assert created == []
    r = client.post("/api/v1/kb/registry/standards", headers=ADMIN,
                    json={"fields": {"code": "A-1", "name": "x", "applies_to": "y", "confidence": "low"}})
    assert r.status_code == 200
    assert created and created[0][0] == "standards"


def test_kb_base_write_requires_admin(client, monkeypatch):
    import storage.kb_store as ks

    monkeypatch.setattr(ks, "create_base",
                        lambda name, description=None, created_by=None, is_default=False:
                        {"id": "b1", "name": name, "description": description})
    r = client.post("/api/v1/kb/bases", headers=DESIGNER, json={"name": "d-kb"})
    assert r.status_code == 403
    r = client.post("/api/v1/kb/bases", headers=ADMIN, json={"name": "a-kb"})
    assert r.status_code == 200


# ----------------------------------------------------------------------
# 硬规则②补强：管理面（LLM 供应商 reload）= admin
# ----------------------------------------------------------------------
def test_llm_provider_reload_requires_admin(client, monkeypatch):
    import api.main as main_mod

    reloaded = []
    monkeypatch.setattr(main_mod, "reload_providers", lambda: reloaded.append(True))
    assert client.post("/llm/provider/reload", headers=DESIGNER).status_code == 403
    assert client.post("/llm/provider/reload", headers=NO_ROLES).status_code == 403
    assert client.post("/llm/provider/reload", headers=ADMIN).status_code == 200
    assert reloaded == [True]
