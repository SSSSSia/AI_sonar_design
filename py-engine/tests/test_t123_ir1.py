# -*- coding: utf-8 -*-
"""T12.3 单测：IR1 人工核对修订（编辑窗口守卫/追加版本/重派 A2）。存储层 monkeypatch。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}
BODY = {
    "objectives": [{"dimension": "声阵规模", "value": 24, "explicit": True},
                   {"dimension": "工作深度", "value": 300, "explicit": True}],
    "missing_items": ["频段未明确"],
    "reason": "用户核对修订",
}


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture()
def patched(monkeypatch):
    from api import cad as cad_mod
    from storage import ir_store

    state = {
        "session": {"id": "s-1", "pipeline_status": "paused", "current_stage": "gate1"},
        "ir1": {"objectives": [{"dimension": "声阵规模", "value": 16, "explicit": False}],
                "missing_items": ["频段未明确", "口径未给出"],
                "sub_queries": ["q1", "q2", "q3", "q4"], "retrieved_context": []},
        "dispatched": [],
        "invalidated": [],
        "saved": [],
    }
    monkeypatch.setattr(ir_store, "get_session", lambda sid: state["session"])
    monkeypatch.setattr(ir_store, "get_latest_snapshot",
                        lambda sid, t: state["ir1"] if t == "IR1" else None)
    monkeypatch.setattr(ir_store, "save_snapshot",
                        lambda sid, t, c, created_by: state["saved"].append((t, c, created_by)) or 3)
    monkeypatch.setattr(ir_store, "mark_invalidated",
                        lambda sid, types: state["invalidated"].extend(types) or types)

    class _A2:
        @staticmethod
        def delay(sid):
            state["dispatched"].append(("a2", sid))

    import worker.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "a2_task", _A2)
    return state


def test_revise_happy_path(client, patched):
    r = client.post("/api/v1/cad/sessions/s-1/irs/IR1/revisions", headers=ADMIN, json=BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ir1_version"] == 3 and body["a2_redispatched"] is True
    assert body["invalidated"] == ["IR2", "IR3", "IR4", "IR5"]
    assert patched["dispatched"] == [("a2", "s-1")]
    t, content, created_by = patched["saved"][-1]
    assert t == "IR1" and created_by == "user"
    assert all(o["explicit"] is True for o in content["objectives"])  # 人工修订视为显式
    assert content["sub_queries"] == ["q1", "q2", "q3", "q4"]  # 未修订字段保留


def test_revise_window_guards(client, patched):
    for status, stage in (("running", "A1"), ("paused", "gate2"), ("completed", "A5"),
                          ("running", "A3")):
        patched["session"] = {"id": "s-1", "pipeline_status": status, "current_stage": stage}
        r = client.post("/api/v1/cad/sessions/s-1/irs/IR1/revisions", headers=ADMIN, json=BODY)
        assert r.status_code == 409, f"{status}@{stage} 应不可编辑"
    # 窗口内两态可编辑
    patched["session"] = {"id": "s-1", "pipeline_status": "running", "current_stage": "A2"}
    assert client.post("/api/v1/cad/sessions/s-1/irs/IR1/revisions",
                       headers=ADMIN, json=BODY).status_code == 200
    patched["session"] = {"id": "s-1", "pipeline_status": "paused", "current_stage": "gate1"}
    assert client.post("/api/v1/cad/sessions/s-1/irs/IR1/revisions",
                       headers=ADMIN, json=BODY).status_code == 200


def test_revise_empty_objectives_rejected(client, patched):
    r = client.post("/api/v1/cad/sessions/s-1/irs/IR1/revisions", headers=ADMIN,
                    json={**BODY, "objectives": []})
    assert r.status_code == 400
