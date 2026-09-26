# -*- coding: utf-8 -*-
"""T8.3 微调 API 单测（PHASE-008）：会话/轮次/长轮询/回传/上传定稿/定稿放弃全链路（API 级）。

真库 + 真桶（固定 uuid 段 3000000000NN，teardown 级联清理 + MinIO 前缀清理）；
Runner 一律 monkeypatch（不打 LLM）：no-op 或脚本化"模拟插件"驱动工具协议。
"""

from __future__ import annotations

import json
import uuid as uuid_mod

import pytest
from fastapi.testclient import TestClient
from jsonschema import validate as schema_validate

from api.main import app
from storage import ir_store, object_store, tune_store
from storage.tune_store import _connect

DESIGN_SID = "00000000-0000-0000-0000-300000000001"
HDRS = {"X-User-Id": "t83", "X-User-Roles": "admin"}
MIN_IR3 = {
    "cadquery_script": "# t83 baseline\nresult = cq.Workplane('XY').box(100, 20, 20)",
    "step_file": {"bucket": "step-models", "object_key": f"{DESIGN_SID}/sonar_model.step"},
    "features": [],
    "geometry_summary": {"source": "extracted", "bbox_mm": [100.0, 20.0, 20.0],
                         "volume_mm3": 40000.0, "part_count": 1, "detail": "t83 单测占位"},
}


@pytest.fixture(scope="module", autouse=True)
def env():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO design_sessions (id, project_id, pipeline_status, title)
               SELECT %s::uuid, p.id, 'paused', 't83-微调API单测' FROM projects p LIMIT 1
               ON CONFLICT (id) DO NOTHING""",
            (DESIGN_SID,),
        )
    ir_store.save_snapshot(DESIGN_SID, "IR3", MIN_IR3, created_by="t83")
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ir_snapshots WHERE session_id = %s", (DESIGN_SID,))
        cur.execute("DELETE FROM design_sessions WHERE id = %s", (DESIGN_SID,))
    # MinIO 测试产物清理（cad-tunes/{DESIGN_SID}/ 前缀）
    try:
        from app_config import BUCKET_CAD_TUNES

        cli = object_store._get_client()
        names = [o.object_name for o in cli.list_objects(BUCKET_CAD_TUNES, prefix=f"{DESIGN_SID}/", recursive=True)]
        if names:
            for _e in cli.remove_objects(BUCKET_CAD_TUNES, names):
                pass
    except Exception:
        pass


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def no_llm(monkeypatch):
    """Runner 不真跑：记录 spawn 调用，轮次保持 running（供协议分支测试）。"""
    spawned: list[tuple] = []
    monkeypatch.setattr("cad_agent.service.spawn_round", lambda tune, rnd: spawned.append((tune["id"], rnd["round_no"])))
    return spawned


# ----------------------------------------------------------------------
# 会话创建
# ----------------------------------------------------------------------
def test_create_requires_user_header(client):
    resp = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune")
    assert resp.status_code == 401


def test_create_conflict_when_active(client, no_llm):
    r1 = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune", headers=HDRS)
    assert r1.status_code == 200, r1.text
    tune_id = r1.json()["id"]
    try:
        r2 = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune", headers=HDRS)
        assert r2.status_code == 409
        assert tune_id in r2.json()["detail"]
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)


def test_create_after_close_allowed_again(client, no_llm):
    r1 = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune", headers=HDRS)
    tune_id = r1.json()["id"]
    client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)
    r2 = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune", headers=HDRS)
    assert r2.status_code == 200
    client.post(f"/api/v1/cad-agent/tune/{r2.json()['id']}/close", headers=HDRS)


# ----------------------------------------------------------------------
# 消息与工具协议
# ----------------------------------------------------------------------
def _active_tune(client) -> str:
    r = client.post(f"/api/v1/cad-agent/sessions/{DESIGN_SID}/tune", headers=HDRS)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_message_creates_round_and_blocks_concurrent(client, no_llm):
    tune_id = _active_tune(client)
    try:
        r = client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS,
                        json={"text": "在基座加一圈法兰", "selection": {"kind": "Face", "area_mm2": 120.0}})
        assert r.status_code == 200, r.text
        rnd = r.json()
        assert rnd["round_no"] == 1 and rnd["status"] == "running"
        assert rnd["baseline_ir_version"] == ir_store.get_latest_version(DESIGN_SID, "IR3")
        assert no_llm and no_llm[0][1] == 1

        # 上轮 running → 并发消息 409
        r2 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS, json={"text": "再改"})
        assert r2.status_code == 409
        # 空文本 400
        r3 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS, json={"text": "  "})
        assert r3.status_code == 400
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)


def test_tool_call_longpoll_result_rejects_late(client, no_llm):
    tune_id = _active_tune(client)
    try:
        rnd = client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS,
                          json={"text": "加孔阵"}).json()
        # 模拟 Runner 下发工具调用（服务端协议同款：tune_store 建行）
        from cad_agent.service import protocol_execute  # 仅确认可导入；此处直接建行

        call = tune_store.create_tool_call(rnd["id"], "execute_edit_code", {"code": "result = model"})
        # 空等轮询：wait=0 立即返回 pending 调用
        r = client.get(f"/api/v1/cad-agent/tune/{tune_id}/tool-calls", params={"cursor": 0, "wait": 0},
                       headers=HDRS)
        assert r.status_code == 200
        body = r.json()
        assert [c["call_id"] for c in body["calls"]] == [call["id"]]
        assert body["cursor"] == call["id"]
        # cursor 越过后为空
        r2 = client.get(f"/api/v1/cad-agent/tune/{tune_id}/tool-calls",
                        params={"cursor": call["id"], "wait": 0}, headers=HDRS)
        assert r2.json()["calls"] == []

        # 回传结果 → done；迟到回传 409
        r3 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/tool-calls/{call['id']}/result", headers=HDRS,
                         json={"ok": True, "quality": {"status": "pass"}, "doc_summary": "bbox ok"})
        assert r3.status_code == 200
        r4 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/tool-calls/{call['id']}/result", headers=HDRS,
                         json={"ok": True})
        assert r4.status_code == 409
        assert "迟到" in r4.json()["detail"]
        # 不存在的调用 404
        r5 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/tool-calls/999999/result", headers=HDRS,
                         json={"ok": True})
        assert r5.status_code == 404
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)


# ----------------------------------------------------------------------
# 定稿上传 → IR3 版本追加
# ----------------------------------------------------------------------
def test_upload_version_appends_ir3(client, no_llm):
    tune_id = _active_tune(client)
    try:
        rnd = client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS,
                          json={"text": "加厚 5mm"}).json()
        # 非 awaiting_upload 拒绝上传
        r_bad = client.post(f"/api/v1/cad-agent/tune/{tune_id}/versions", headers=HDRS,
                            data={"round_no": str(rnd["round_no"]), "script": "result = model"},
                            files={"step_file": ("m.step", b"garbage")})
        assert r_bad.status_code == 409
        tune_store.update_round(rnd["id"], status="awaiting_upload", script="result = model")

        before = ir_store.get_latest_version(DESIGN_SID, "IR3")
        # 垃圾 STEP：几何提取如实降级，但 IR3 版本仍追加（管线继续，A-16）
        r = client.post(f"/api/v1/cad-agent/tune/{tune_id}/versions", headers=HDRS,
                        data={"round_no": str(rnd["round_no"])},
                        files={"step_file": ("m.step", b"ISO-10303-21; garbage not a real step"),
                               "fcstd_file": ("m.FCStd", b"fake-fcstd-bytes")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ir_version"] == before + 1
        assert body["geometry_summary"]["source"] == "extraction_failed"

        # 轮次回填 done + IR3 内容核对
        got = tune_store.get_round(tune_id, rnd["round_no"])
        assert got["status"] == "done" and got["result_ir3_version"] == before + 1
        new_ir3 = ir_store.get_snapshot(DESIGN_SID, "IR3", before + 1)
        assert new_ir3["step_file"]["bucket"] == "cad-tunes"
        assert "CAD 微调 r1" in new_ir3["cadquery_script"]
        assert "t83 baseline" in new_ir3["cadquery_script"]  # 基线脚本保留（溯源链）
        assert new_ir3["geometry_summary"]["detail"].startswith("人工微调 r1")
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)


def test_finalize_and_message_blocked(client, no_llm):
    tune_id = _active_tune(client)
    r = client.post(f"/api/v1/cad-agent/tune/{tune_id}/finalize", headers=HDRS, json={"note": "定稿交审"})
    assert r.status_code == 200 and r.json()["status"] == "finalized"
    # 定稿后不可继续对话 / 重复定稿 409
    assert client.post(f"/api/v1/cad-agent/tune/{tune_id}/messages", headers=HDRS,
                       json={"text": "x"}).status_code == 409
    assert client.post(f"/api/v1/cad-agent/tune/{tune_id}/finalize", headers=HDRS).status_code == 409


# ----------------------------------------------------------------------
# 手动上传（设计师视口直接调整 → manual 轮次承接 → IR3 新版本）
# ----------------------------------------------------------------------
def test_manual_upload_appends_ir3(client, no_llm):
    tune_id = _active_tune(client)
    try:
        before = ir_store.get_latest_version(DESIGN_SID, "IR3")
        # 无任何对话轮次也可上传：服务端自动建 manual 轮次承接
        r = client.post(f"/api/v1/cad-agent/tune/{tune_id}/versions/manual", headers=HDRS,
                        files={"step_file": ("m.step", b"ISO-10303-21; garbage not a real step"),
                               "fcstd_file": ("m.FCStd", b"fake-fcstd-bytes")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ir_version"] == before + 1
        assert body["geometry_summary"]["source"] == "extraction_failed"

        rounds = tune_store.list_rounds(tune_id)
        assert len(rounds) == 1 and rounds[0]["created_by"] == "manual"
        assert rounds[0]["status"] == "done" and rounds[0]["result_ir3_version"] == before + 1
        new_ir3 = ir_store.get_snapshot(DESIGN_SID, "IR3", before + 1)
        assert "手动上传" in new_ir3["cadquery_script"]
        assert "t83 baseline" in new_ir3["cadquery_script"]  # 基线脚本保留（溯源链）
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)

    # 微调结束后不可手动上传（409）
    r2 = client.post(f"/api/v1/cad-agent/tune/{tune_id}/versions/manual", headers=HDRS,
                     files={"step_file": ("m.step", b"x")})
    assert r2.status_code == 409


# ----------------------------------------------------------------------
# 上下文端点 + WS 契约
# ----------------------------------------------------------------------
def test_context_returns_presigned_url(client, no_llm):
    tune_id = _active_tune(client)
    try:
        r = client.get(f"/api/v1/cad-agent/tune/{tune_id}/context", headers=HDRS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["step_url"].startswith("http")
        assert body["version"] == ir_store.get_latest_version(DESIGN_SID, "IR3")
        assert isinstance(body["geometry_summary"], str) and body["geometry_summary"]
    finally:
        client.post(f"/api/v1/cad-agent/tune/{tune_id}/close", headers=HDRS)


def test_cad_tune_updated_message_matches_ws_contract():
    with open(__import__("pathlib").Path(__file__).resolve().parents[2]
              / "contracts" / "ws" / "ws-message.schema.json", encoding="utf-8") as f:
        schema = json.load(f)
    msg = {
        "type": "cad_tune_updated",
        "session_id": DESIGN_SID,
        "timestamp": "2026-09-24T00:00:00+00:00",
        "payload": {"tune_id": str(uuid_mod.uuid4()), "state": "version_ready",
                    "round_no": 2, "ir_version": "5"},
    }
    schema_validate(msg, schema)
    # 非法 state 必须被契约拒绝
    bad = dict(msg, payload={"tune_id": "x", "state": "unknown"})
    with pytest.raises(Exception):
        schema_validate(bad, schema)
