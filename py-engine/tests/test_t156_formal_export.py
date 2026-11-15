# -*- coding: utf-8 -*-
"""T15.6 / C-1 正式导出内核单测（真库，固定标记 teardown 清理）。

验收锚（DEV-001 §3）：
  工作稿/正式版标识（文件名前缀+封面类别+组合章）/ 无有效报告=明确原因拒绝 /
  历史有效组合可导出 / 内容完整性哈希校验 / Word-PDF 同素材口径。

组合哈希口径 = sha256(content::text)（PG jsonb 规范文本，与 Java pgjdbc getString
同源字节，ADR-014 §5 修订 1）——测试侧经 get_snapshot_full 取同源文本求哈希。
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid as uuid_mod
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from api.main import app
from storage import ir_store
from storage.ir_store import _connect

SID = "00000000-0000-0000-0000-600000000156"
TITLE = "t156-正式导出单测"
HDRS = {"X-User-Id": "t156", "X-User-Roles": "admin"}
IR2 = {"design_document": "# 方案\n拖曳阵参数化设计", "parameters": [
    {"id": "P-001", "name": "声阵段外径", "value": 27, "unit": "mm",
     "source_type": "formula", "source_ref": "r=D/2", "confidence": 0.9}]}
IR3 = {"cadquery_script": "# t156", "features": [],
       "geometry_summary": {"source": "extracted", "bbox_mm": [1, 2, 3],
                            "volume_mm3": 6.0, "part_count": 1, "detail": "t156 占位"}}
IR4 = {"overall": "incomplete", "issues": [
    {"constraint_id": "CHK-SEM-DIAMETER", "severity": "unverified",
     "related_params": ["P-001"], "related_features": []}], "csr": 80}


def _content_sha(sid: str, ir_type: str, version: int) -> str:
    snap = ir_store.get_snapshot_full(sid, ir_type, version)
    return hashlib.sha256(snap["content_text"].encode("utf-8")).hexdigest()


def _combo_metadata(sid: str, *, bad_hash: bool = False) -> dict:
    combo = {}
    for t, v in (("IR2", 1), ("IR3", 1), ("IR4", 1)):
        sha = "0" * 64 if bad_hash else _content_sha(sid, t, v)
        combo[t] = {"snapshot_id": f"{t}-{v}", "version": v,
                    "content_sha256": sha, "valid_at_decision": "2026-11-15T00:00:00+08:00"}
    return {"contract": "ADR-014§5", "policy_version": "1", "snapshot_combo": combo,
            "combo_policy": {"ir4_overall": "incomplete",
                             "exception": None,
                             "unresolved": [{"source": "IR4", "ref": "CHK-SEM-DIAMETER"}]}}


def _insert_record(metadata: dict | None, gate: str = "gate2",
                   decision: str = "approved") -> str:
    rid = str(uuid_mod.uuid4())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO review_records (id, session_id, gate, decision, annotations, metadata)
               VALUES (%s, %s, %s, %s, '[]'::jsonb, %s::jsonb)""",
            (rid, SID, gate, decision,
             json.dumps(metadata, ensure_ascii=False) if metadata is not None else None))
        conn.commit()
    return rid


@pytest.fixture(scope="module", autouse=True)
def env():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO design_sessions (id, project_id, pipeline_status, title)
               SELECT %s::uuid, p.id, 'completed', %s FROM projects p LIMIT 1
               ON CONFLICT (id) DO NOTHING""",
            (SID, TITLE))
    for t, content in (("IR2", IR2), ("IR3", IR3), ("IR4", IR4)):
        ir_store.save_snapshot(SID, t, content, created_by="t156")
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM review_records WHERE session_id = %s", (SID,))
        cur.execute("DELETE FROM ir_snapshots WHERE session_id = %s", (SID,))
        cur.execute("DELETE FROM design_sessions WHERE id = %s", (SID,))
        conn.commit()


@pytest.fixture()
def client():
    return TestClient(app)


# ----------------------------------------------------------------------
# 工作稿（不带 record_id）：latest 读取 + 「工作稿_」前缀（原行为不回归）
# ----------------------------------------------------------------------
def test_draft_export_uses_latest_and_prefix(client):
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx", headers=HDRS)
    assert resp.status_code == 200
    filename = unquote(resp.headers["content-disposition"])
    assert filename.startswith("attachment") and "工作稿_设计方案_" in filename
    assert f"_IR2v1.docx" in filename


def test_draft_export_404_without_ir2(client):
    resp = client.get(
        f"/api/v1/cad/sessions/{uuid_mod.uuid4()}/export/docx", headers=HDRS)
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# 正式批准版：组合校验链
# ----------------------------------------------------------------------
def test_formal_happy_path_combo_chapter_and_prefix(client):
    rid = _insert_record(_combo_metadata(SID))
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                      params={"record_id": rid}, headers=HDRS)
    assert resp.status_code == 200
    filename = unquote(resp.headers["content-disposition"])
    assert "正式批准_设计方案_" in filename
    assert "_IR2v1-IR3v1-IR4v1.docx" in filename
    import docx as docx_mod

    doc = docx_mod.Document(io.BytesIO(resp.content))
    texts = [p.text for p in doc.paragraphs] + [
        c.text for t in doc.tables for row in t.rows for c in row.cells]
    full = "\n".join(texts)
    assert "批准版本组合" in full, "正式版必须含组合章"
    assert "正式批准版" in full, "封面类别标注"
    assert "CHK-SEM-DIAMETER" in full, "未决项显性入档渲染"
    assert "v1" in full and "内容哈希" in full


def test_formal_record_not_found_404(client):
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                      params={"record_id": str(uuid_mod.uuid4())}, headers=HDRS)
    assert resp.status_code == 404
    assert "批准记录不存在" in resp.json()["detail"]


def test_formal_rejects_non_gate2_or_non_approved(client):
    rid = _insert_record(_combo_metadata(SID), gate="gate1")
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                      params={"record_id": rid}, headers=HDRS)
    assert resp.status_code == 409 and "gate2" in resp.json()["detail"]

    rid2 = _insert_record(_combo_metadata(SID), decision="rejected")
    resp2 = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                       params={"record_id": rid2}, headers=HDRS)
    assert resp2.status_code == 409 and "gate2" in resp2.json()["detail"]


def test_formal_rejects_legacy_record_without_combo(client):
    rid = _insert_record(None)  # C-0 契约前历史记录：metadata NULL
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                      params={"record_id": rid}, headers=HDRS)
    assert resp.status_code == 409
    assert "无组合凭证" in resp.json()["detail"]


def test_formal_rejects_invalidated_combo(client):
    rid = _insert_record(_combo_metadata(SID))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ir_snapshots SET invalidated_at = now() "
            "WHERE session_id = %s AND ir_type = 'IR4'", (SID,))
        conn.commit()
    try:
        resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                          params={"record_id": rid}, headers=HDRS)
        assert resp.status_code == 409
        assert "已失效" in resp.json()["detail"] and "IR4" in resp.json()["detail"]
    finally:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ir_snapshots SET invalidated_at = NULL "
                "WHERE session_id = %s AND ir_type = 'IR4'", (SID,))
            conn.commit()


def test_formal_rejects_hash_mismatch(client):
    rid = _insert_record(_combo_metadata(SID, bad_hash=True))
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/docx",
                      params={"record_id": rid}, headers=HDRS)
    assert resp.status_code == 409
    assert "哈希不符" in resp.json()["detail"]


def test_formal_pdf_same_resolution_503_without_gotenberg(client, monkeypatch):
    """正式 PDF 与 Word 同一素材解析（校验通过后进转换）；gotenberg 未配置 → 503 如实。"""
    monkeypatch.delenv("DOCGEN_PDF_ENDPOINT", raising=False)
    rid = _insert_record(_combo_metadata(SID))
    resp = client.get(f"/api/v1/cad/sessions/{SID}/export/pdf",
                      params={"record_id": rid}, headers=HDRS)
    assert resp.status_code == 503  # 组合校验已通过（否则 409），拒绝只来自转换服务缺失
    assert "PDF 转换" in resp.json()["detail"]
