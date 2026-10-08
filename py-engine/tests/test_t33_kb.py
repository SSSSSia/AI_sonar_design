# -*- coding: utf-8 -*-
"""T3.3 知识资产库单测：三库 CRUD/A4 接线/LLM 供应商管理/首样播种解析。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AUTH = {"X-User-Id": "tester", "X-User-Roles": "admin"}


# ----------------------------------------------------------------------
# recompute_dynamic（库内公式复算底座）
# ----------------------------------------------------------------------
def test_recompute_dynamic_matches_registry():
    from solver import recompute

    result = recompute.recompute_dynamic(
        expr="10*log(n, 10)", symbols={"n": "阵元数"}, defaults={},
        inputs={"n": 16}, formula_name="DI", unit="dB",
    )
    assert abs(result.value - 12.0411998266) < 1e-6
    assert "SymPy 复算" in result.derivation


# ----------------------------------------------------------------------
# A4 接线：库内公式可被定位并复算；军标库条目登记提示
# ----------------------------------------------------------------------
def test_a4_uses_db_formula_when_registry_misses(monkeypatch):
    from agents.a4_validation.agent import A4Agent

    rows = [{"key": "di_custom", "name": "指向性指数 DI", "expr": "10*log(n, 10)",
             "symbols": ["n"], "unit": "dB"}]
    monkeypatch.setattr("storage.registry_store.list_rows_cached", lambda kind: rows if kind == "formulas" else [])

    ir2 = {"design_document": "# x", "parameters": [{
        "id": "P-001", "name": "指向性指数 DI", "value": 12.0412, "unit": "dB",
        "source_type": "formula", "source_ref": "业务库#di_custom",
        "derivation": "指向性指数 DI = n=16 → 代入计算得 12.0412 dB",
        "confidence": "high", "trace_id": "tr-x",
    }]}
    ir3 = {"cadquery_script": "import cadquery as cq\n", "step_file":
           {"bucket": "step-models", "object_key": "s/x.step"}, "features": []}
    ir4 = A4Agent().run(ir2, ir3)
    # 库内公式复算一致 → 无 error；几何未联动 → unverified → incomplete（T14.4）
    assert not [i for i in ir4["issues"] if i["severity"] == "error"]
    assert ir4["overall"] == "incomplete"
    assert ir4["csr"] == 100.0
    assert ir4["coverage"] == {"checked": 1, "applicable": 1, "ratio": 100.0}


def test_a4_db_formula_tampered_value_still_error(monkeypatch):
    from agents.a4_validation.agent import A4Agent

    rows = [{"key": "di_custom", "name": "指向性指数 DI", "expr": "10*log(n, 10)",
             "symbols": ["n"], "unit": "dB"}]
    monkeypatch.setattr("storage.registry_store.list_rows_cached", lambda kind: rows if kind == "formulas" else [])
    ir2 = {"design_document": "# x", "parameters": [{
        "id": "P-001", "name": "指向性指数 DI", "value": 20.0, "unit": "dB",
        "source_type": "formula", "source_ref": "业务库#di_custom",
        "derivation": "指向性指数 DI = n=16 → 代入计算得 12.0412 dB",
        "confidence": "high", "trace_id": "tr-x",
    }]}
    ir3 = {"cadquery_script": "import cadquery as cq\n", "step_file":
           {"bucket": "step-models", "object_key": "s/x.step"}, "features": []}
    ir4 = A4Agent().run(ir2, ir3)
    assert ir4["overall"] == "fail"
    assert any(i["suggestion"] == "back_to_A2" for i in ir4["issues"])


def test_standards_db_entry_registered_pending(monkeypatch):
    """库内军标（check=None）：命中出 pending 提示（A-16 如实），非 violation。"""
    from solver import standards

    monkeypatch.setattr(standards, "load_enabled_standards", lambda max_age_s=30: [
        standards.StandardSpec(code="GJB 585", name="水声设备术语", applies_to="灵敏度", check=None),
    ])
    result = standards.check_param({"name": "换能器灵敏度", "value": -195})
    assert result["status"] == "pending"
    assert "GJB 585" in result["message"] and "无判定表达式" in result["message"]


def test_standards_registry_check_still_violates(monkeypatch):
    from solver import standards

    spec = standards.StandardSpec(
        code="GJB-TEST", name="测试", applies_to="灵敏度",
        check=lambda p: None if float(p["value"]) > -200 else "灵敏度不得低于 -200dB",
    )
    monkeypatch.setattr(standards, "load_enabled_standards", lambda max_age_s=30: [])
    standards.REGISTRY_STANDARDS.append(spec)
    try:
        bad = standards.check_param({"name": "换能器灵敏度", "value": -210})
        assert bad["status"] == "violation"
    finally:
        standards.REGISTRY_STANDARDS.remove(spec)


# ----------------------------------------------------------------------
# 首样播种解析（确定性）
# ----------------------------------------------------------------------
def test_first_sample_seed_registries(monkeypatch):
    from knowledge import first_sample

    created: list[tuple[str, dict]] = []
    import storage.registry_store as rs

    monkeypatch.setattr(rs, "create_row",
                        lambda kind, fields: created.append((kind, fields)) or fields)

    kb_text = (
        "### 声学约束公式\n"
        "- **指向性指数**: DI ≈ 10 × lg(N)，N=总阵元数\n"
        "### 设计约束\n"
        "### 硬约束（任何方案均不可违反）\n"
        "1. **直径约束**：声阵段外径不超过27mm（半径13.5mm）\n"
        "2. **工作深度**：不小于300m\n"
        "### 派生约束（随规模变化需重新计算）\n"
    )
    seeded = first_sample._seed_registries(kb_text)
    formulas = [f for k, f in created if k == "formulas"]
    constraints = [c for k, c in created if k == "constraints"]
    assert seeded["formulas"] == len(formulas) and seeded["formulas"] >= 5  # solver REGISTRY 全量播种
    assert all(f["confidence"] == "high" and f["enabled"] for f in formulas)
    assert [c["name"] for c in constraints] == ["直径约束", "工作深度"]
    assert "27mm" in constraints[0]["expression"]


# ----------------------------------------------------------------------
# LLM 供应商管理 API（fake 存储层）
# ----------------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def fake_providers(monkeypatch):
    rows: dict[str, dict] = {}
    reloaded: list[bool] = []
    import storage.registry_store as rs

    monkeypatch.setattr(rs, "list_providers", lambda: list(rows.values()))
    monkeypatch.setattr(rs, "create_provider",
                        lambda name, base_url, api_key, model, priority=0, timeout_s=300, enabled=True:
                        rows.setdefault(name, {"name": name, "base_url": base_url, "model": model,
                                               "enabled": enabled, "priority": priority,
                                               "timeout_s": timeout_s}))
    monkeypatch.setattr(rs, "update_provider",
                        lambda name, fields: rows[name].update(
                            {k: v for k, v in fields.items() if v is not None}) or rows[name])
    monkeypatch.setattr(rs, "delete_provider", lambda name: rows.pop(name, None) is not None)
    monkeypatch.setattr("llm.adapter.reload_providers", lambda: reloaded.append(True))
    return {"rows": rows, "reloaded": reloaded}


def test_provider_crud_masked_and_hot_reload(client, fake_providers):
    r = client.post("/api/v1/kb/llm-providers", headers=AUTH, json={
        "name": "mock", "base_url": "http://127.0.0.1:8091/v1",
        "api_key": "sk-secret-never-echo", "model": "m1"})
    assert r.status_code == 200, r.text
    assert r.json()["reloaded"] is True
    assert fake_providers["reloaded"] == [True]
    # 列表响应不含 api_key 字段（脱敏红线）
    r = client.get("/api/v1/kb/llm-providers")
    assert "sk-secret-never-echo" not in r.text
    assert "api_key" not in r.text
    # PATCH 启停
    r = client.patch("/api/v1/kb/llm-providers/mock", headers=AUTH, json={"enabled": False})
    assert r.status_code == 200 and r.json()["provider"]["enabled"] is False
    # DELETE
    r = client.delete("/api/v1/kb/llm-providers/mock", headers=AUTH)
    assert r.status_code == 200
    r = client.delete("/api/v1/kb/llm-providers/mock", headers=AUTH)
    assert r.status_code == 404


def test_registry_api_validation(client, monkeypatch):
    created: list[tuple[str, dict]] = []
    import storage.registry_store as rs

    monkeypatch.setattr(rs, "create_row",
                        lambda kind, fields: created.append((kind, fields)) or {"id": 1, **fields})
    # confidence 校验（create_row 内部调用；此处直接对验证函数断言）
    with pytest.raises(ValueError):
        rs._validate_confidence("最高")
    assert rs._validate_confidence("high") == "high"
    # 未知库 → 404
    r = client.get("/api/v1/kb/registry/unknown", headers=AUTH)
    assert r.status_code == 404
    # 正常创建
    r = client.post("/api/v1/kb/registry/standards", headers=AUTH,
                    json={"fields": {"code": "GJB-X", "name": "x", "applies_to": "y",
                                     "confidence": "high"}})
    assert r.status_code == 200
    assert created and created[0][0] == "standards"


def test_writer_role_required_for_registry(client, monkeypatch):
    import storage.registry_store as rs

    monkeypatch.setattr(rs, "list_rows", lambda kind, enabled_only=False: [])
    assert client.get("/api/v1/kb/registry/standards").status_code == 401  # 无信任头
    r = client.get("/api/v1/kb/registry/standards", headers={"X-User-Id": "v", "X-User-Roles": "viewer"})
    assert r.status_code == 200  # 读放行
    r = client.post("/api/v1/kb/registry/standards", headers={"X-User-Id": "v", "X-User-Roles": "viewer"},
                    json={"fields": {"code": "X"}})
    assert r.status_code == 403  # viewer 无写权限
