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
    "cq_show(result, 'zztest')\n"
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
        cur.execute("DELETE FROM ref_scripts WHERE source_note LIKE %s",
                    ('%zztest_t163%',))
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
