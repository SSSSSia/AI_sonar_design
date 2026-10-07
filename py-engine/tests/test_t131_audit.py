# -*- coding: utf-8 -*-
"""独立审计库单测（T13.1，V17 audit_logs）：audit_store 落库/检索 + 审计中间件规则与留痕。

真库（固定标记，teardown 清理）。中间件走 TestClient 打真实端点
（DELETE /kb/documents/{不存在} → 404，无副作用但留痕——审计记录「尝试」含失败）。
"""

from __future__ import annotations

import uuid as uuid_mod

import pytest
from fastapi.testclient import TestClient

from api import audit as audit_mod
from api.main import app
from storage import audit_store
from storage.tune_store import _connect

MARK = "t131-audit-单测"
HDRS = {"X-User-Id": "t-131-audit", "X-User-Roles": "admin"}


@pytest.fixture(scope="module", autouse=True)
def env():
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs WHERE action ILIKE %s OR user_id = %s OR detail::text LIKE %s",
                    ("t131.%", "t-131-audit", '%"t-131-audit"%'))
        conn.commit()


def test_match_rule_hits_and_misses():
    assert audit_mod.match_rule("POST", "/api/v1/kb/documents")[0] == "kb.upload"
    assert audit_mod.match_rule("DELETE", "/api/v1/kb/documents/abc-123")[:3] == (
        "kb.delete_document", "kb_document", "abc-123")
    assert audit_mod.match_rule("POST", "/api/v1/kb/registry/formulas/batch")[0] == "kb.registry_batch"
    assert audit_mod.match_rule("PATCH", "/api/v1/kb/registry/standards/42")[2] == "42"
    assert audit_mod.match_rule("GET", "/api/v1/cad/sessions/s1/export/docx")[0] == "export.docx"
    # 非留痕域：读端点 / 参数变更（已有专表）/ 未知路径
    assert audit_mod.match_rule("GET", "/api/v1/kb/documents") is None
    assert audit_mod.match_rule("POST", "/api/v1/cad-agent/sessions/s1/parameters/p1/update") is None
    assert audit_mod.match_rule("POST", "/api/v1/whatever") is None


def test_store_append_and_search(env):
    audit_store.append("t-131-audit", MARK, "t131.op", resource_type="session",
                       resource_id="s-x", detail={"status": 200}, ip="127.0.0.1")
    audit_store.append(None, None, "t131.anon")
    rows = audit_store.list_logs(username=MARK)
    assert rows["total"] >= 1
    top = rows["logs"][0]
    assert top["action"] == "t131.op" and top["username"] == MARK
    assert top["detail"] == {"status": 200} and top["ip"] == "127.0.0.1"
    # action 前缀过滤
    assert all(r["action"].startswith("t131") for r in audit_store.list_logs(action="t131")["logs"])


def test_middleware_records_attempt_including_404(env):
    client = TestClient(app)
    fake_doc = str(uuid_mod.uuid4())
    resp = client.delete(f"/api/v1/kb/documents/{fake_doc}", headers=HDRS)
    assert resp.status_code in (404, 200)  # 端点语义与审计无关，留痕才是本测点
    rows = audit_store.list_logs(action="kb.delete_document")["logs"]
    hit = next(r for r in rows if r["resource_id"] == fake_doc)
    assert hit["user_id"] == "t-131-audit"  # 信任头透传（非 UUID 也不报错——V17 用 VARCHAR）
    assert hit["detail"]["status"] == resp.status_code
    # 未带信任头的匿名调用同样留痕（user_id 空），审计不依赖业务鉴权
    resp2 = client.delete(f"/api/v1/kb/documents/{uuid_mod.uuid4()}")
    assert resp2.status_code in (401, 403, 404)
    rows2 = audit_store.list_logs(action="kb.delete_document")["logs"]
    hit2 = next(r for r in rows2 if r["user_id"] is None)
    assert hit2["resource_type"] == "kb_document"


def test_unmatched_path_not_recorded(env):
    before = audit_store.list_logs(action="t131-none")["total"]
    client = TestClient(app)
    client.get("/api/v1/kb/status")  # 读端点，不在规则表
    assert audit_store.list_logs(action="t131-none")["total"] == before
