# -*- coding: utf-8 -*-
"""T14.5 单测：产物版本化与报告失效全覆盖（外部审查问题 4a/4b）。

回归验收案例：
  08 报告过期：微调上传定稿后，旧 IR4/IR5 立即失效（不经"应用提案"路径）；
  10 历史产物：A3 连续生成使用互不相同的对象键（旧 STEP 不被覆盖），引用带 sha256。
存储层 monkeypatch，无真实 DB/MinIO。
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}


# ----------------------------------------------------------------------
# 用例 10：A3 产物路径版本化 + sha256
# ----------------------------------------------------------------------
def test_a3_artifact_paths_unique_and_hashed(monkeypatch, tmp_path):
    from agents.a3_cad import agent as a3_mod

    uploaded = []

    def fake_upload(local_path, bucket, key):
        uploaded.append(key)
        return {"bucket": bucket, "object_key": key, "sha256": "ab" * 32}

    monkeypatch.setattr(a3_mod.object_store, "upload_artifact", fake_upload)
    monkeypatch.setattr(a3_mod.object_store, "upload_text",
                        lambda text, bucket, key: uploaded.append(key) or
                        {"bucket": bucket, "object_key": key})

    step = tmp_path / "m.step"
    step.write_text("ISO-10303-21;")
    for _ in range(2):
        a3_mod._upload_artifacts("sess-1", step, "import cadquery as cq\n")

    step_keys = [k for k in uploaded if k.endswith(".step")]
    assert len(step_keys) == 2 and step_keys[0] != step_keys[1], "两次生成不得共用对象键"
    assert all(k.startswith("sess-1/a3/") for k in step_keys)
    assert not any(k == "sess-1/sonar_model.step" for k in uploaded), "固定路径已废弃"


def test_upload_artifact_returns_sha256(monkeypatch, tmp_path):
    """object_store.upload_artifact / upload_bytes 返回内容哈希（T14.5 契约）。"""
    from storage import object_store

    monkeypatch.setattr(object_store, "_ensure_buckets", lambda: None)

    class _C:
        def fput_object(self, bucket, key, path):
            pass

        def put_object(self, bucket, key, stream, length, content_type):
            pass

    monkeypatch.setattr(object_store, "_get_client", lambda: _C())
    f = tmp_path / "x.step"
    f.write_bytes(b"hello-step")
    ref = object_store.upload_artifact(f, "step-models", "s/a3/t/m.step")
    import hashlib

    assert ref["sha256"] == hashlib.sha256(b"hello-step").hexdigest()
    ref2 = object_store.upload_bytes(b"hello-step", "step-models", "s/other.step")
    assert ref2["sha256"] == ref["sha256"]


# ----------------------------------------------------------------------
# 用例 08：微调定稿 → 旧 IR4/IR5 失效（不经提案应用路径）
# ----------------------------------------------------------------------
@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


def test_finalize_invalidates_ir4_ir5(monkeypatch):
    """微调轮定稿（对话轮与 manual 共用 finalize_version）：IR3 落新版本后立即
    定向失效 IR4/IR5——忽略提案/无提案路径下旧报告不再以现行身份存在。"""
    from cad_agent import service
    from storage import ir_store

    state = {"saved": [], "invalidated": []}
    tune = {"id": "t-1", "design_session_id": "s-1", "status": "active"}
    rnd = {"id": 9, "status": "awaiting_upload", "script": "result = model"}
    monkeypatch.setattr(service.tune_store, "get_round", lambda tid, no: rnd)
    monkeypatch.setattr("storage.object_store.upload_bytes",
                        lambda data, bucket, key, content_type="application/octet-stream":
                        {"bucket": bucket, "object_key": key})
    monkeypatch.setattr(service, "_inspect_step_file", lambda p: None)  # 提取禁用 → 降级如实
    monkeypatch.setattr(ir_store, "get_latest_snapshot", lambda sid, t: {})
    monkeypatch.setattr(ir_store, "save_snapshot",
                        lambda sid, t, c, created_by: state["saved"].append(t) or 7)
    monkeypatch.setattr(ir_store, "mark_invalidated",
                        lambda sid, types: state["invalidated"].extend(types) or types)
    monkeypatch.setattr(service.tune_store, "update_round", lambda rid, **kw: None)
    monkeypatch.setattr(service, "_notify_ir_updated", lambda *a, **k: None)
    monkeypatch.setattr(service, "_notify_tune", lambda *a, **k: None)
    monkeypatch.setattr(service, "_generate_param_proposals", lambda *a, **k: None)

    out = service.finalize_version(tune, 1, b"ISO-10303-21;", None, None)
    assert out["ir_version"] == 7
    assert state["saved"] == ["IR3"]
    assert sorted(state["invalidated"]) == ["IR4", "IR5"]
    assert out["invalidated"] == ["IR4", "IR5"]


def test_ir3_schema_allows_sha256():
    """file_ref.sha256 加法演进：带哈希引用合法；旧两键引用仍合法。"""
    from ir_contract import validate_ir

    base = {"cadquery_script": "x=1", "features": [],
            "geometry_summary": {"source": "extraction_failed", "detail": "d"}}
    ir3 = validate_ir("IR3", {**base, "step_file": {
        "bucket": "step-models", "object_key": "s/a3/t/m.step",
        "sha256": "ab" * 32}})
    assert ir3["step_file"]["sha256"] == "ab" * 32
    validate_ir("IR3", {**base, "step_file": {
        "bucket": "step-models", "object_key": "s/old.step"}})  # 旧形态不拒
