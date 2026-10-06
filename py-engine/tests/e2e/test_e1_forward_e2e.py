# -*- coding: utf-8 -*-
"""PHASE-012 T12.2 E1 正向联动 e2e：改 IR2 参数 → A3 重新建模 → A4 重算 → gate2 复审 → 完成。

基线设计会话/IR2/IR3 直接落库构造（paused@gate2），真实链路走 API：
正向更新（100→140mm）→ IR2 v2 + 定向失效 IR3/IR4/IR5 → a3 重派（mock LLM 重新建模）
→ A4 重算 → 自动重挂 gate2 → 批准 → A5 → completed。传播深度记录落 V16 表。

前置：gateway/workflow/py-api/py-worker/数据底座可达（LLM 由本测试自带 mock 满足）。
运行（py-engine 目录）：python -m pytest tests/e2e/test_e1_forward_e2e.py -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"
SID = "00000000-0000-0000-0000-600000000001"
PROVIDER_NAME = "mock-e1f-e2e"


def _pg_exec(sql: str, fetch: bool = False):
    import psycopg

    from app_config import DATABASE_URL

    with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _login() -> str:
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "X-User-Id": "t122-e2e-admin", "X-User-Roles": "admin"}


def _wait_session(token: str, status: str, stage: str, timeout_s: float = 240.0) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{SID}", headers=_h(token), timeout=10)
        if r.status_code == 200:
            last = r.json().get("data", {})
            if last.get("pipelineStatus") == status and last.get("currentStage") == stage:
                return last
        time.sleep(1.5)
    raise AssertionError(f"会话 {timeout_s}s 内未进入 {status}@{stage}，最后: {last}")


@pytest.fixture(scope="module")
def env():
    from storage import ir_store, object_store
    from app_config import BUCKET_STEP_MODELS

    try:
        httpx.post(f"{GATEWAY}/api/v1/auth/login", json={}, timeout=5)
        httpx.get(f"{PYAPI}/healthz", timeout=5)
    except Exception as exc:
        pytest.skip(f"e2e 前置不满足（服务不可达）: {exc}")

    port = _free_port()
    mock_log = os.path.join(tempfile.gettempdir(), "t122_mock_llm.log")
    mock_err = os.path.join(tempfile.gettempdir(), "t122_mock_llm.err")
    Path(mock_log).write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.mock_llm_server"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "MOCK_LLM_PORT": str(port), "MOCK_LLM_LOG": mock_log},
        stdout=subprocess.DEVNULL, stderr=open(mock_err, "w"),
    )
    mock_host = os.environ.get("CAD_TUNE_MOCK_HOST", "127.0.0.1")
    base_url = f"http://{mock_host}:{port}"
    ready = False
    for _ in range(30):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2).json().get("tune_routing"):
                ready = True
                break
        except Exception:
            time.sleep(1)
    if not ready:
        proc.terminate()
        pytest.skip("mock LLM 未就绪")

    _pg_exec(
        f"""INSERT INTO llm_providers (name, base_url, api_key, model, enabled, priority, timeout_s)
            VALUES ('{PROVIDER_NAME}', '{base_url}/v1', 'sk-e2e-e1f', 'mock', true, 1000, 60)
            ON CONFLICT (name) DO UPDATE SET enabled = true, priority = 1000, base_url = '{base_url}/v1'""",
    )
    httpx.post(f"{PYAPI}/llm/provider/reload", timeout=5)

    # ---- 基线：paused@gate2 + IR2 v1（P-901=100）+ IR3 v1（真实 STEP）----
    _pg_exec(
        f"""INSERT INTO design_sessions (id, project_id, pipeline_status, current_stage, title)
            SELECT '{SID}'::uuid, p.id, 'paused', 'gate2', 't122-e1正向e2e'
            FROM projects p LIMIT 1
            ON CONFLICT (id) DO UPDATE SET pipeline_status = 'paused', current_stage = 'gate2'""",
    )
    from ir_contract import validate_ir

    step_path = str(Path(tempfile.gettempdir()) / "t122_baseline.step")
    import cadquery as cq

    cq.exporters.export(cq.Workplane("XY").box(100, 20, 20), step_path, exportType="STEP")
    step_ref = object_store.upload_bytes(Path(step_path).read_bytes(),
                                         BUCKET_STEP_MODELS, f"{SID}/t122_baseline.step",
                                         content_type="application/step")
    ir_store.save_snapshot(SID, "IR2", validate_ir("IR2", {
        "design_document": "# 拖曳阵方案\n\n正文。",
        "parameters": [{
            "id": "P-901", "name": "基段长度", "value": 100, "unit": "mm",
            "source_type": "estimated", "source_ref": "任务书",
            "derivation": "基段长度 len=100 mm，按任务书取中值",
            "confidence": "medium", "trace_id": "t-e1f-901",
        }],
    }), created_by="t122")
    ir_store.save_snapshot(SID, "IR3", validate_ir("IR3", {
        "cadquery_script": "# t122 baseline\nresult = cq.Workplane('XY').box(100, 20, 20)",
        "step_file": step_ref,
        "features": [{
            "feature_id": "F-001", "segment": "SEG-01",
            "geometry": {"bbox_mm": [100.0, 20.0, 20.0], "volume_mm3": 40000.0,
                         "center_mm": [0.0, 0.0, 0.0], "linked_dim": 0},
            "linked_param_id": "P-901",
        }],
        "geometry_summary": {"source": "extracted", "bbox_mm": [100.0, 20.0, 20.0],
                             "volume_mm3": 40000.0, "part_count": 1, "detail": "t122 基线"},
    }), created_by="t122")

    yield {"sid": SID, "token": _login()}

    _pg_exec(f"DELETE FROM traceability_chain WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM review_records WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM cad_param_updates WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM ir_snapshots WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM design_sessions WHERE id = '{SID}'")
    for bucket in ("step-models", "cad-scripts", "cad-previews"):
        try:
            cli = object_store._get_client()
            names = [o.object_name for o in cli.list_objects(bucket, prefix=f"{SID}/", recursive=True)]
            if names:
                for e in cli.remove_objects(bucket, names):
                    pass
        except Exception:
            pass
    _pg_exec(f"DELETE FROM llm_providers WHERE name = '{PROVIDER_NAME}'")
    try:
        httpx.post(f"{PYAPI}/llm/provider/reload", timeout=5)
    except Exception:
        pass
    proc.terminate()


def test_e1_forward_full_loop(env):
    token = env["token"]
    from storage import ir_store

    # ① 正向更新 P-901：100 → 140
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/parameters/P-901/update",
                   headers=_h(token), json={"value": 140, "reason": "任务书修订（e2e）"}, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ir2_version"] == 2 and body["depth"] == 1 and body["a3_redispatched"] is True
    # 种子基线只有 IR2/IR3——实际失效以已存在快照为准（IR4/IR5 由重跑的 A4/A5 产出）
    assert set(body["invalidated"]) == {"IR3"}

    # ② IR2 v2 内容：value + derivation 同步 + 正向修订段
    ir2_v2 = ir_store.get_snapshot(SID, "IR2", 2)
    param = ir2_v2["parameters"][0]
    assert param["value"] == 140
    assert "len=140" in param["derivation"]
    assert "修订记录（E1 正向参数更新）" in ir2_v2["design_document"]

    # ③ A3 重新建模（mock LLM）→ A4 重算 → 自动重挂 gate2
    # 韧性：套跑环境偶发 worker→host mock 瞬态拒连（2026-10-06 套跑实测一次，独立复跑即绿）；
    # a3 失败时 FAIL 事件被 paused 态拒绝（409）→ 会话停在 paused@A3——同参重发一次即可 redrive。
    try:
        _wait_session(token, "paused", "gate2", timeout_s=240)
    except AssertionError:
        r2 = httpx.post(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/parameters/P-901/update",
                        headers=_h(token), json={"value": 140, "reason": "redrive（瞬态重试）"}, timeout=20)
        assert r2.status_code == 200, f"redrive 失败: {r2.text}"
        _wait_session(token, "paused", "gate2", timeout_s=240)
    assert ir_store.get_latest_version(SID, "IR3") >= 2  # A3 产出新 IR3
    assert ir_store.get_latest_snapshot(SID, "IR4") is not None  # A4 重算落 IR4

    # ④ 传播深度留痕（V16）
    rows = _pg_exec(f"SELECT depth, origin FROM cad_param_updates WHERE session_id = '{SID}'",
                    fetch=True)
    assert rows and rows[0][0] == 1 and rows[0][1] == "user_forward"

    # ⑤ 审阅关②批准（复审）→ A5 → completed（E1 正向闭环终点）
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{SID}/reviews/gate2",
                   headers=_h(token), json={"decision": "approved"}, timeout=15)
    assert r.status_code == 200, r.text
    _wait_session(token, "completed", "A5", timeout_s=240)
