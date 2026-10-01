# -*- coding: utf-8 -*-
"""PHASE-011 T11.3 E1 反向回写 e2e：微调定稿 diff 提案 → 确认回写 IR2 → IR4 重算 → gate2 复审。

基线设计会话/IR2/IR3 直接落库构造（含 linked_param_id 联动特征，确定性），
真实 STEP（cadquery 生成）走 manual 上传触发 finalize_version → E1 提案生成；
确认回写后断言：IR2 v2（value+derivation 同步+修订段）、A4 重算落 IR4、
会话自动重挂 gate2、批准后走完 A5 到 completed——E1 全闭环。

前置：gateway/workflow/py-api/py-worker/数据底座可达（LLM 由本测试自带 mock 满足）。
运行（py-engine 目录）：python -m pytest tests/e2e/test_e1_reverse_write_e2e.py -v
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
SID = "00000000-0000-0000-0000-500000000001"
PROVIDER_NAME = "mock-e1-e2e"


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


def _build_step(path: str, length: float = 100.0) -> None:
    """cadquery 生成真实 STEP：x 向长度可变（= 联动参数 dimension 0）。"""
    import cadquery as cq

    model = cq.Workplane("XY").box(length, 20, 20)
    cq.exporters.export(model, path, exportType="STEP")


def _login() -> str:
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "X-User-Id": "t113-e2e-admin", "X-User-Roles": "admin"}


def _wait_session(token: str, status: str, stage: str, timeout_s: float = 120.0) -> dict:
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


def _ir2_content() -> dict:
    from ir_contract import validate_ir

    return validate_ir("IR2", {
        "design_document": "# 拖曳阵方案\n\n## 参数表\n\n## 溯源\n",
        "parameters": [{
            "id": "P-901", "name": "基段长度", "value": 100, "unit": "mm",
            "source_type": "estimated", "source_ref": "任务书",
            "derivation": "基段长度 len=100 mm，按任务书区间取中值",
            "confidence": "medium", "trace_id": "t-e1-901",
        }],
    })


def _ir3_content(step_ref: dict, length: float) -> dict:
    """带联动特征的基线 IR3（A3 产出形态：linked_param_id + linked_dim）。"""
    from ir_contract import validate_ir

    return validate_ir("IR3", {
        "cadquery_script": f"# t113 baseline\nresult = cq.Workplane('XY').box({length}, 20, 20)",
        "step_file": step_ref,
        "features": [{
            "feature_id": "F-001", "segment": "SEG-01",
            "geometry": {"bbox_mm": [length, 20.0, 20.0], "volume_mm3": length * 400.0,
                         "center_mm": [0.0, 0.0, 0.0], "linked_dim": 0},
            "linked_param_id": "P-901",
        }],
        "geometry_summary": {"source": "extracted", "bbox_mm": [length, 20.0, 20.0],
                             "volume_mm3": length * 400.0, "part_count": 1,
                             "detail": "t113 e2e 基线（联动特征）"},
    })


@pytest.fixture(scope="module")
def env():
    from storage import ir_store, object_store
    from app_config import BUCKET_STEP_MODELS

    try:
        httpx.post(f"{GATEWAY}/api/v1/auth/login", json={}, timeout=5)
        httpx.get(f"{PYAPI}/healthz", timeout=5)
    except Exception as exc:
        pytest.skip(f"e2e 前置不满足（服务不可达）: {exc}")

    # ---- 自带 mock LLM（A5 需要；复用 t87 模式：随机端口+独立 provider 行）----
    port = _free_port()
    mock_log = os.path.join(tempfile.gettempdir(), "t113_mock_llm.log")
    mock_err = os.path.join(tempfile.gettempdir(), "t113_mock_llm.err")
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
            VALUES ('{PROVIDER_NAME}', '{base_url}/v1', 'sk-e2e-e1', 'mock', true, 1000, 60)
            ON CONFLICT (name) DO UPDATE SET enabled = true, priority = 1000, base_url = '{base_url}/v1'""",
    )
    httpx.post(f"{PYAPI}/llm/provider/reload", timeout=5)

    # ---- 基线：设计会话（paused@gate2）+ IR2 v1（登记 len=100）+ IR3 v1（联动特征）----
    _pg_exec(
        f"""INSERT INTO design_sessions (id, project_id, pipeline_status, current_stage, title)
            SELECT '{SID}'::uuid, p.id, 'paused', 'gate2', 't113-e1-e2e'
            FROM projects p LIMIT 1
            ON CONFLICT (id) DO UPDATE SET pipeline_status = 'paused', current_stage = 'gate2'""",
    )
    step_path = str(Path(tempfile.gettempdir()) / "t113_baseline.step")
    _build_step(step_path)
    step_ref = object_store.upload_bytes(Path(step_path).read_bytes(),
                                         BUCKET_STEP_MODELS, f"{SID}/t113_baseline.step",
                                         content_type="application/step")
    ir_store.save_snapshot(SID, "IR2", _ir2_content(), created_by="t113")
    ir_store.save_snapshot(SID, "IR3", _ir3_content(step_ref, 100.0), created_by="t113")

    yield {"sid": SID, "token": _login(), "base_ir3_version": ir_store.get_latest_version(SID, "IR3")}

    # ---- 清理（提案表经 design_sessions 级联；微调表经 CASCADE）----
    _pg_exec(f"DELETE FROM traceability_chain WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM review_records WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM cad_tune_tool_calls WHERE round_id IN "
             f"(SELECT r.id FROM cad_tune_rounds r JOIN cad_tune_sessions t ON t.id = r.tune_session_id "
             f"WHERE t.design_session_id = '{SID}')")
    _pg_exec(f"DELETE FROM cad_tune_rounds WHERE tune_session_id IN "
             f"(SELECT id FROM cad_tune_sessions WHERE design_session_id = '{SID}')")
    _pg_exec(f"DELETE FROM cad_tune_sessions WHERE design_session_id = '{SID}'")
    _pg_exec(f"DELETE FROM ir_snapshots WHERE session_id = '{SID}'")
    _pg_exec(f"DELETE FROM design_sessions WHERE id = '{SID}'")
    for bucket in ("step-models", "cad-tunes", "cad-scripts", "cad-previews"):
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


def test_e1_reverse_write_full_loop(env):
    token = env["token"]
    base_v = env["base_ir3_version"]
    from storage import ir_store

    # ① 建微调会话（基线 = 落库构造的 IR3 v1，P-901 联动 SEG-01 dim0=100mm）
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/tune", headers=_h(token), timeout=15)
    assert r.status_code == 200, r.text
    tune_id = r.json()["id"]
    assert r.json()["baseline_ir_version"] == base_v

    # ② 上传改动的真实 STEP（100→120mm）→ manual 轮 finalize → E1 提案生成
    step_path = str(Path(tempfile.gettempdir()) / "t113_tuned.step")
    _build_step(step_path, length=120.0)
    with open(step_path, "rb") as f:
        r = httpx.post(f"{PYAPI}/api/v1/cad-agent/tune/{tune_id}/versions/manual",
                       headers=_h(token), files={"step_file": ("tuned.step", f, "application/step")},
                       timeout=60)
    assert r.status_code == 200, r.text
    tuned_v = r.json()["ir_version"]
    assert tuned_v == base_v + 1

    # ③ 提案应生成：100 → 120（+20%）
    r = httpx.get(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/param-proposals",
                  params={"status": "proposed"}, headers=_h(token), timeout=10)
    assert r.status_code == 200, r.text
    proposals = r.json()["proposals"]
    assert len(proposals) == 1, f"期望 1 条提案，实际 {proposals}"
    p = proposals[0]
    assert p["param_id"] == "P-901" and p["old_value"] == 100 and p["new_value"] == 120
    assert p["delta_pct"] == 20.0 and p["ir3_version"] == tuned_v

    # ④ 确认回写 → IR2 v2 + A4 重派
    r = httpx.post(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/param-proposals/confirm",
                   headers=_h(token), json={"proposal_ids": [p["id"]]}, timeout=15)
    assert r.status_code == 200, r.text
    resp = r.json()
    assert resp["applied"] == 1 and resp["ir2_version"] == 2 and resp["redispatched"] is True

    # ⑤ IR2 v2 内容：value/derivation 同步 + design_document 修订段
    ir2_v2 = ir_store.get_snapshot(SID, "IR2", 2)
    param = ir2_v2["parameters"][0]
    assert param["value"] == 120
    assert "len=120" in param["derivation"] and "len=100" not in param["derivation"]
    from solver.recompute import parse_inputs_from_derivation

    assert parse_inputs_from_derivation(param["derivation"])["len"] == 120.0
    assert "修订记录（E1 CAD→文档反向回写）" in ir2_v2["design_document"]

    # ⑥ A4 重算 → 自动重挂 gate2（复用 pauseAtGate 乐观条件，复审链路）
    _wait_session(token, "paused", "gate2", timeout_s=120)

    # ⑦ 审阅关②批准（复审通过）→ A5 → completed（E1 闭环终点）
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{SID}/reviews/gate2",
                   headers=_h(token), json={"decision": "approved"}, timeout=15)
    assert r.status_code == 200, r.text
    _wait_session(token, "completed", "A5", timeout_s=180)

    # ⑧ 提案终态 applied
    r = httpx.get(f"{PYAPI}/api/v1/cad-agent/sessions/{SID}/param-proposals",
                  headers=_h(token), timeout=10)
    assert r.json()["proposals"][0]["status"] == "applied"
    assert r.json()["proposals"][0]["applied_ir2_version"] == 2
