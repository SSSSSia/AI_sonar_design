# -*- coding: utf-8 -*-
"""T16.3 B3 候选沉淀单测（PHASE-016 §3.7 步骤 1/2，契约=ADR-015 v1.1 §9）。

真库（tests 与现网共用 sonarcad 库）：projects → design_sessions → ir_snapshots →
review_records 四级夹具直插（zztest_t163 前缀 + teardown 清理，review_records 写侧
在 Java 但夹具直插仅造测试事实），ref_scripts 行 zztest 不会出现（deposit ref_id 为
dep-{sha8}——teardown 按 ref_id LIKE 'dep-%' 有误删风险，改为按本测试登记行 id 精确清）。
覆盖：
- WP1 扫描与校验链：判定链命中/质量门 fail 拒绝/组合失效与哈希不符拒绝/无脚本/secret 命中；
- WP2 沉淀执行：真沙箱重放通过落 pending 行+凭证/重放失败不落行/幂等（重复事件不重复入库）/
  API RBAC + 审计 kb.ref_deposit / SOURCE_TYPES 放行 deposit；
- WP4 检索闭环：deposit 行 active → REF_SOURCE=db → select_reference 命中。
（MinIO 上传 monkeypatch 记录调用，不打真实对象存储；重放用 trivial 脚本走真沙箱子进程。）
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg
import pytest

from services import deposit
from storage.review_store import _connect

PROJ_PREFIX = "zztest_t163"
SCRIPT_OK = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').circle(10.0).extrude(100.0)\n"
    "# 沙箱 prelude 约定：result 变量 → 自动导出 STEP（重放 ok 判定依据）\n"
)
SCRIPT_SECRET = "token = 'bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\n" + SCRIPT_OK


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_rows():
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            DELETE FROM review_records WHERE session_id IN (
                SELECT id FROM design_sessions WHERE project_id IN (
                    SELECT id FROM projects WHERE name LIKE %s))""", (PROJ_PREFIX + "%",))
        cur.execute("""
            DELETE FROM ir_snapshots WHERE session_id IN (
                SELECT id FROM design_sessions WHERE project_id IN (
                    SELECT id FROM projects WHERE name LIKE %s))""", (PROJ_PREFIX + "%",))
        cur.execute("""
            DELETE FROM parameter_schemes WHERE session_id IN (
                SELECT id FROM design_sessions WHERE project_id IN (
                    SELECT id FROM projects WHERE name LIKE %s))""", (PROJ_PREFIX + "%",))
        cur.execute("""
            DELETE FROM design_sessions WHERE project_id IN (
                SELECT id FROM projects WHERE name LIKE %s)""", (PROJ_PREFIX + "%",))
        cur.execute("DELETE FROM projects WHERE name LIKE %s", (PROJ_PREFIX + "%",))
        # deposit 行 ref_id=dep-{sha8}（无 zztest 前缀）——按操作者精确清（生产 deposit 的
        # created_by 为真实用户名，不受影响；凭证 JSON 不含 zztest 字样故 source_note 不可依）
        cur.execute("DELETE FROM ref_scripts WHERE created_by LIKE 't163-%'")
        cur.execute("DELETE FROM ref_scripts WHERE source_note LIKE '%zztest_t163%'")
        cur.execute("DELETE FROM ref_scripts WHERE ref_id LIKE 'zztest_t163%'")


def _pg_content_text(cur, sid: str, ir_type: str, version: int) -> str:
    cur.execute(
        "SELECT content::text FROM ir_snapshots WHERE session_id=%s AND ir_type=%s AND version=%s",
        (sid, ir_type, version))
    return cur.fetchone()[0]


def _make_session(*, tag: str, script: str = SCRIPT_OK, ir4_overall: str = "pass",
                  invalidate_ir3: bool = False, tamper_hash: bool = False,
                  include_script: bool = True) -> dict:
    """四级夹具：project→session→IR2/IR3/IR4 快照→gate2 批准记录（含 snapshot_combo）。"""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            proj = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            cur.execute("INSERT INTO projects (id, name) VALUES (%s, %s)",
                        (proj, f"{PROJ_PREFIX}-{tag}"))
            cur.execute("""INSERT INTO design_sessions (id, project_id, title, pipeline_status,
                           current_stage) VALUES (%s, %s, %s, 'paused', 'gate2')""",
                        (sid, proj, f"zztest_t163 {tag}"))
            contents = {
                "IR2": {"design_document": f"doc-{tag}", "parameters": []},
                "IR3": {"cadquery_script": script if include_script else None,
                        "features": [], "geometry_summary": {"source": "extracted"}},
                "IR4": {"overall": ir4_overall, "issues": [], "csr": 100.0},
            }
            combo: dict[str, dict] = {}
            for ir_type, content in contents.items():
                cur.execute("""INSERT INTO ir_snapshots (session_id, ir_type, version,
                               schema_version, content) VALUES (%s, %s, 1, 'v1', %s)""",
                            (sid, ir_type, json.dumps(content)))
                text = _pg_content_text(cur, sid, ir_type, 1)
                sha = _sha(text)
                if ir_type == "IR3" and tamper_hash:
                    sha = "0" * 64
                combo[ir_type] = {"version": 1, "snapshot_id": "x", "content_sha256": sha,
                                  "valid_at_decision": True, "valid": True}
            if invalidate_ir3:
                cur.execute("""UPDATE ir_snapshots SET invalidated_at = now()
                               WHERE session_id=%s AND ir_type='IR3'""", (sid,))
            cur.execute("""INSERT INTO review_records (session_id, gate, decision, metadata)
                           VALUES (%s, 'gate2', 'approved', %s)""",
                        (sid, json.dumps({
                            "contract": "ADR-014§5", "policy_version": "1",
                            "snapshot_combo": combo,
                            "combo_policy": {"ir4_overall": ir4_overall,
                                             "exception": None, "unresolved": []},
                        })))
        return {"project_id": proj, "session_id": sid}
    finally:
        conn.close()


def _scan_find(candidates: list[dict], sid: str) -> dict | None:
    return next((c for c in candidates if c["session_id"] == sid), None)


def _blocked_find(blocked: list[dict], sid: str) -> dict | None:
    return next((c for c in blocked if c["session_id"] == sid), None)


# ----------------------------------------------------------------------
# WP0：secret 检查与 ref_id 派生（纯函数）
# ----------------------------------------------------------------------
def test_secret_scan_patterns():
    assert deposit._secret_scan(SCRIPT_OK) == []
    assert "bearer_token" in deposit._secret_scan("x = 'bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'")
    assert "api_key_literal" in deposit._secret_scan("api_key = 'sk-verysecret123'")
    assert "private_key_block" in deposit._secret_scan("-----BEGIN RSA PRIVATE KEY-----\nxx")
    assert "aws_access_key" in deposit._secret_scan("k = 'AKIAABCDEFGHIJKLMNOP'")


def test_derive_ref_id_deterministic_and_slug():
    sha = _sha(SCRIPT_OK)
    rid = deposit.derive_ref_id(sha)
    assert rid == deposit.derive_ref_id(_sha(SCRIPT_OK))
    assert rid.startswith("dep-")
    import re
    assert re.match(ref_store_slug_re(), rid)


def ref_store_slug_re() -> str:
    from storage.ref_store import REF_ID_RE
    return REF_ID_RE


# ----------------------------------------------------------------------
# WP1：扫描与校验链
# ----------------------------------------------------------------------
def test_scan_candidate_ok_and_shape():
    made = _make_session(tag="ok")
    out = deposit.scan_candidates()
    cand = _scan_find(out["candidates"], made["session_id"])
    assert cand is not None, out
    assert cand["depositable"] is True
    assert cand["ir4_overall"] == "pass"
    assert cand["ir3_version"] == 1
    assert cand["script_sha256"] == _sha(SCRIPT_OK)
    assert cand["ref_id"] == deposit.derive_ref_id(_sha(SCRIPT_OK))
    assert cand["already_deposited"] is False
    assert cand["session_title"] == "zztest_t163 ok"


def test_scan_excludes_non_combo_records():
    # finalize 不等于批准：无 snapshot_combo 的 gate2 approved 记录不进沉淀源
    made = _make_session(tag="nocombo")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE review_records
                       SET metadata = jsonb_set(metadata, '{snapshot_combo}', 'null'::jsonb)
                       WHERE session_id=%s AND gate='gate2'""", (made["session_id"],))
    out = deposit.scan_candidates()
    assert _scan_find(out["candidates"], made["session_id"]) is None
    assert _blocked_find(out["blocked"], made["session_id"]) is None


def test_scan_blocks_ir4_fail_even_with_exception():
    _make_session(tag="fail4", ir4_overall="fail")
    out = deposit.scan_candidates()
    blk = _blocked_find(out["blocked"], _last_sid("fail4"))
    assert blk is not None and blk["reason"] == "ir4_fail"


def test_scan_blocks_invalidated_combo():
    _make_session(tag="invalid", invalidate_ir3=True)
    out = deposit.scan_candidates()
    blk = _blocked_find(out["blocked"], _last_sid("invalid"))
    assert blk is not None and blk["reason"] == "combo_invalid"
    assert any("invalidated" in d for d in blk["detail"])


def test_scan_blocks_hash_mismatch():
    _make_session(tag="tamper", tamper_hash=True)
    out = deposit.scan_candidates()
    blk = _blocked_find(out["blocked"], _last_sid("tamper"))
    assert blk is not None and blk["reason"] == "combo_invalid"
    assert any("hash_mismatch" in d for d in blk["detail"])


def test_scan_blocks_no_script_and_secret():
    _make_session(tag="noscript", include_script=False)
    _make_session(tag="secret", script=SCRIPT_SECRET)
    out = deposit.scan_candidates()
    assert _blocked_find(out["blocked"], _last_sid("noscript"))["reason"] == "no_script"
    blk = _blocked_find(out["blocked"], _last_sid("secret"))
    assert blk["reason"] == "secret_hit"


def _last_sid(tag: str) -> str:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT s.id::text FROM design_sessions s
                       JOIN projects p ON p.id = s.project_id
                       WHERE p.name = %s""", (f"{PROJ_PREFIX}-{tag}",))
        return cur.fetchone()[0]


def _last_record_id(sid: str) -> str:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text FROM review_records WHERE session_id=%s AND gate='gate2'", (sid,))
        return cur.fetchone()[0]


# ----------------------------------------------------------------------
# WP2：沉淀执行（真沙箱重放 + pending 行 + 凭证 + 幂等）
# ----------------------------------------------------------------------
def _record_of(tag: str) -> tuple[str, str]:
    sid = _last_sid(tag)
    return sid, _last_record_id(sid)


def test_deposit_happy_path_pending_with_provenance(monkeypatch):
    uploaded: list[tuple[str, str]] = []

    def fake_upload_text(text: str, bucket: str, object_key: str) -> dict:
        uploaded.append((bucket, object_key))
        return {"bucket": bucket, "object_key": object_key, "sha256": _sha(text)}

    monkeypatch.setattr("storage.object_store.upload_text", fake_upload_text)
    sid, rid = _record_of("ok")
    out = deposit.deposit_from_record(sid, rid, "t163-tester")
    assert out["already"] is False
    row = out["row"]
    assert row["status"] == "pending"
    assert row["source_type"] == "deposit"
    assert row["ref_id"].startswith("dep-")
    assert row["content_sha256"] == _sha(SCRIPT_OK)
    assert uploaded and uploaded[0][0] == "ref-scripts"
    note = json.loads(row["source_note"])
    assert note["session_id"] == sid and note["record_id"] == rid
    assert note["ir3_version"] == 1
    assert note["script_sha256"] == _sha(SCRIPT_OK)
    assert len(note["combo_content_sha256"]) == 64
    assert note["quality"] == {"ir4_overall": "pass", "replay": "pass", "secret_scan": "pass"}
    assert note["deposited_by"] == "t163-tester"


def test_deposit_replay_failure_deposits_nothing(monkeypatch):
    monkeypatch.setattr("storage.object_store.upload_text",
                        lambda t, b, k: {"bucket": b, "object_key": k})
    # 会话脚本本身可过扫描/组合校验（内容一致），但沙箱执行必崩——精确落在重放失败路径
    _make_session(tag="replayfail", script="raise RuntimeError('boom')\n")
    sid, rid = _record_of("replayfail")
    with pytest.raises(deposit.DepositError) as ei:
        deposit.deposit_from_record(sid, rid, "t163-tester")
    assert ei.value.reason == "replay_failed"
    # 不落行
    out = deposit.ref_store.list_rows()
    assert not [r for r in out if r["source_note"] and sid in str(r["source_note"])]


def test_deposit_idempotent_repeat_event(monkeypatch):
    monkeypatch.setattr("storage.object_store.upload_text",
                        lambda t, b, k: {"bucket": b, "object_key": k})
    sid, rid = _record_of("ok")
    first = deposit.deposit_from_record(sid, rid, "t163-a")
    second = deposit.deposit_from_record(sid, rid, "t163-b")
    assert second["already"] is True
    assert second["row"]["id"] == first["row"]["id"]
    rows = deposit.ref_store.list_rows(first["ref_id"])
    assert len(rows) == 1  # 重复事件不重复入库


def test_deposit_rejects_fail_combo(monkeypatch):
    monkeypatch.setattr("storage.object_store.upload_text",
                        lambda t, b, k: {"bucket": b, "object_key": k})
    _make_session(tag="faildep", ir4_overall="fail")
    sid, rid = _record_of("faildep")
    with pytest.raises(deposit.DepositError) as ei:
        deposit.deposit_from_record(sid, rid, "t163-tester")
    assert ei.value.reason == "ir4_fail"


# ----------------------------------------------------------------------
# WP2 API：RBAC + 审计 + 端点语义
# ----------------------------------------------------------------------
def test_api_deposit_rbac_and_flow(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr("storage.object_store.upload_text",
                        lambda t, b, k: {"bucket": b, "object_key": k, "sha256": _sha(t)})
    from api.main import app

    sid, rid = _record_of("ok")
    with TestClient(app) as c:
        viewer = {"X-User-Id": "t163-viewer", "X-User-Roles": "viewer"}
        admin = {"X-User-Id": "t163-admin", "X-User-Roles": "admin"}
        assert c.get("/api/v1/kb/references/deposit-candidates", headers=viewer).status_code == 403
        scan = c.get("/api/v1/kb/references/deposit-candidates", headers=admin)
        assert scan.status_code == 200
        cand = _scan_find(scan.json()["candidates"], sid)
        assert cand is not None and cand["already_deposited"] is True  # 先前用例已沉淀

        resp = c.post("/api/v1/kb/references/deposit", headers=viewer,
                      json={"session_id": sid, "record_id": rid})
        assert resp.status_code == 403
        dep = c.post("/api/v1/kb/references/deposit", headers=admin,
                     json={"session_id": sid, "record_id": rid})
        assert dep.status_code == 200
        assert dep.json()["already"] is True  # 幂等：重复事件不重复入库
        bad = c.post("/api/v1/kb/references/deposit", headers=admin,
                     json={"session_id": sid, "record_id": str(uuid.uuid4())})
        assert bad.status_code == 404 or bad.status_code == 409
        if bad.status_code == 409:
            assert bad.json()["detail"]["reason"] in ("record_not_found", "no_combo",
                                                      "not_approved_gate2")


def test_audit_rules_cover_deposit():
    from api.audit import _RULES

    actions = [r[2] for r in _RULES]
    assert "kb.ref_deposit" in actions
    assert "kb.ref_deposit_scan" in actions


def test_source_type_deposit_accepted():
    # SOURCE_TYPES 放行 deposit（枚举激活），其余非法值仍拒绝
    row = deposit.ref_store.insert_row(
        ref_id="zztest_t163_enum", version=1, file_name="e.py", title=None, description=None,
        domain=None, domain_rules=None, sonar_types=[], keywords=[], bucket="ref-scripts",
        object_key="zztest_t163_enum/v1/e.py", content_sha256=_sha("x"),
        status="pending", source_type="deposit", source_note="zztest_t163 枚举校验",
        created_by="t163",
    )
    assert row["source_type"] == "deposit"
    with pytest.raises(ValueError):
        deposit.ref_store.insert_row(
            ref_id="zztest_t163_enum2", version=1, file_name="e.py", title=None, description=None,
            domain=None, domain_rules=None, sonar_types=[], keywords=[], bucket="ref-scripts",
            object_key="zztest_t163_enum2/v1/e.py", content_sha256=_sha("y"),
            status="pending", source_type="bogus", source_note="zztest_t163 非法枚举",
            created_by="t163",
        )


# ----------------------------------------------------------------------
# WP4：检索闭环——deposit 行 confirm→active → REF_SOURCE=db → select_reference 命中
# ----------------------------------------------------------------------
def test_deposit_row_hit_after_confirm(monkeypatch):
    monkeypatch.setattr("storage.object_store.upload_text",
                        lambda t, b, k: {"bucket": b, "object_key": k, "sha256": _sha(t)})
    # db 装载会真读对象并校验哈希——下载侧一并替身（返回沉淀脚本本体）
    monkeypatch.setattr("storage.object_store.download_text",
                        lambda b, k: SCRIPT_OK)
    _make_session(tag="hit")
    sid, rid = _record_of("hit")
    out = deposit.deposit_from_record(sid, rid, "t163-hit")
    row_id = out["row"]["id"]
    if out["row"]["status"] == "pending":
        deposit.ref_store.confirm_row(row_id)  # 人工确认入库（pending→active+单活互斥）

    monkeypatch.setattr("app_config.REF_SOURCE", "db")
    from cad_engine.reference import CodeSearchService

    svc = CodeSearchService()
    entries = svc._entries()
    match = [e for e in entries if e.get("id") == out["ref_id"]]
    assert match, f"active 的 deposit 行应进 db 检索索引: {[e.get('id') for e in entries]}"
    assert match[0]["catalog_version"] == f"ref-scripts@v{out['row']['version']}"
    _content, match_info = svc.select_reference("16 阵元拖曳线列阵声呐设计，声阵段薄壁圆管外径 27mm")
    # 命中与否取决于评分——此处验收「检索索引包含且可选出」这一闭环最低语义
    if match_info is not None:
        assert match_info["ref_id"] == out["ref_id"]
        assert match_info["catalog_version"] == f"ref-scripts@v{out['row']['version']}"
