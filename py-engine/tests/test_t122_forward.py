# -*- coding: utf-8 -*-
"""T12.2 单测：E1 正向参数更新（API 守卫/传播深度/回写语义）。存储层 monkeypatch，无真实 DB。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def patched(monkeypatch):
    """API 依赖面全 fake：会话/IR2/派发/链深。"""
    from api import cad_agent as api_mod
    from storage import ir_store, param_update_store

    state = {
        "session": {"id": "s-1", "pipeline_status": "paused", "current_stage": "gate2"},
        "ir2": {"design_document": "# 方案", "parameters": [
            {"id": "P-001", "name": "缆长", "value": 100, "unit": "mm", "source_type": "estimated",
             "source_ref": "", "derivation": "len=100 mm", "confidence": "medium", "trace_id": "t"},
        ]},
        "depth": 0,
        "dispatched": [],
        "invalidated": [],
        "saved": [],
    }
    monkeypatch.setattr(ir_store, "get_session", lambda sid: state["session"])
    monkeypatch.setattr(ir_store, "get_latest_snapshot",
                        lambda sid, t: state["ir2"] if t == "IR2" else None)
    monkeypatch.setattr(ir_store, "save_snapshot",
                        lambda sid, t, c, created_by: state["saved"].append((t, c)) or 2)
    monkeypatch.setattr(ir_store, "mark_invalidated",
                        lambda sid, types: state["invalidated"].extend(types) or types)

    class _Store:
        @staticmethod
        def next_depth_or_raise(sid):
            d = state["depth"] + 1
            if d > param_update_store.MAX_PROPAGATION_DEPTH:
                raise ValueError("已达最大传播深度（3）")
            return d

        @staticmethod
        def record(sid, pid, old, new, unit, reason, depth, ir2v, user, origin="user_forward"):
            state["depth"] = depth
            return {"id": "u-1", "depth": depth}

    monkeypatch.setattr(param_update_store, "next_depth_or_raise", _Store.next_depth_or_raise)
    monkeypatch.setattr(param_update_store, "record", _Store.record)

    class _A3:
        @staticmethod
        def delay(sid):
            state["dispatched"].append(("a3", sid))

    import worker.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "a3_task", _A3)
    return state


def test_update_happy_path(client, patched):
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-001/update", headers=ADMIN,
                    json={"value": 140, "reason": "任务书修订"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ir2_version"] == 2 and body["depth"] == 1 and body["a3_redispatched"] is True
    assert patched["invalidated"] == ["IR3", "IR4", "IR5"]  # 定向失效含 IR3（正向特有）
    assert patched["dispatched"] == [("a3", "s-1")]
    new_ir2 = patched["saved"][-1][1]
    param = new_ir2["parameters"][0]
    assert param["value"] == 140
    assert "len=140" in param["derivation"] and "len=100" not in param["derivation"]
    assert "修订记录（E1 正向参数更新）" in new_ir2["design_document"]


def test_update_requires_gate2_pause(client, patched, monkeypatch):
    patched["session"] = {"id": "s-1", "pipeline_status": "completed", "current_stage": "A5"}
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-001/update", headers=ADMIN,
                    json={"value": 140})
    assert r.status_code == 409 and "审阅关②" in r.json()["detail"]
    patched["session"] = {"id": "s-1", "pipeline_status": "paused", "current_stage": "gate1"}
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-001/update", headers=ADMIN,
                    json={"value": 140})
    assert r.status_code == 409


def test_update_param_missing_and_bad_value(client, patched):
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-999/update", headers=ADMIN,
                    json={"value": 140})
    assert r.status_code == 404
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-001/update", headers=ADMIN,
                    json={"value": -5})
    assert r.status_code == 400


def test_depth_cap_rejected(client, patched):
    patched["depth"] = 3  # 链已满
    r = client.post("/api/v1/cad-agent/sessions/s-1/parameters/P-001/update", headers=ADMIN,
                    json={"value": 140})
    assert r.status_code == 409 and "最大传播深度" in r.json()["detail"]


def test_depth_cap_constant():
    from storage.param_update_store import MAX_PROPAGATION_DEPTH

    assert MAX_PROPAGATION_DEPTH == 3  # 系统实施方案 Max Depth=3（行为由 patched API 测试覆盖）
