# -*- coding: utf-8 -*-
"""B3 候选沉淀服务（T16.3，PHASE-016 §3.7 / ADR-015 v1.1 §9）。

沉淀源 = 真实 gate2 批准记录（``gate2 + approved + metadata.snapshot_combo 非空``，
finalize 不等于批准——DEV-001 §8）；判定链与 ``api/cad.py`` C-1 校验链同源，但扫描
场景不抛异常而是逐条如实标注不可沉淀原因（A-16）。九条细则（判定链/质量门/重放/
脱敏/幂等/凭证/确认/审计/检索零改动）见 ADR-015 v1.1 §9，本模块是它们的唯一实现点：
- 质量门：``combo_policy.ir4_overall == "fail"`` → 不可沉淀（例外批准也不行——
  「不能因批准或脚本执行成功直接视为合格范例」；incomplete 为 T14.4 常态放行）；
- 幂等：``ref_id = "dep-{sha8(脚本文本)}"`` 内容派生 → 同内容必同 ref_id，
  ``ref_store.find_by_content`` 命中即已存在（「重复事件不重复入库」）；
- 哈希双口径：脚本内容 sha256(utf-8)（范例库口径，ADR-015 §3）与批准组合的
  content::text 口径（C-0）**不同源勿混用**——两套都落 source_note 凭证各司其职；
- 脱敏：基础 secret 模式检查，命中拒绝不落行；AST 受限泛化本轮不做（DEV-001 §1.3
  「不预设学生交 AST 泛化算法」），title/description 从 IR1/IR2 结构化生成、
  人工确认前可 PATCH 修饰。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from typing import Any

from storage import ir_store, object_store, ref_store, review_store

logger = logging.getLogger("sonarcad.deposit")

# 重放时限（独立于 A3 生成时限——沉淀重放只验证可执行性，短时限够用）
REPLAY_TIME_LIMIT_S = 120

# ------------------------------------------------------------------------------
# 脱敏：基础 secret 模式（命中任一即拒绝沉淀——宁可误拒不落脏范例，A-16 如实报因）
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("api_key_literal", re.compile(r"\b(?:api[_-]?key|apikey)\s*[=:]\s*['\"][^'\"]{8,}", re.I)),
    ("password_literal", re.compile(r"\b(?:password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{4,}", re.I)),
    ("bearer_token", re.compile(r"\bbearer\s+[A-Za-z0-9_\-\.]{20,}", re.I)),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)


def _secret_scan(script: str) -> list[str]:
    """返回命中的 secret 模式名列表（空=通过）。"""
    hits: list[str] = []
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(script):
            hits.append(name)
    return hits


# ------------------------------------------------------------------------------
# 哈希与 ref_id 派生（内容寻址：同内容=同范例，天然幂等）
def script_sha256(script: str) -> str:
    """范例库哈希口径：sha256(脚本文本 utf-8)，与 cad_engine/reference.py 缓存同源。"""
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def derive_ref_id(sha256_hex: str) -> str:
    """``dep-{sha8}``——slug 规则符合 ref_store.REF_ID_RE；内容寻址幂等的根。"""
    return f"dep-{sha256_hex[:8]}"


def _script_of(sid: str, ir3_version: int) -> tuple[str | None, str | None]:
    """取组合版本 IR3 的 cadquery_script；(script, error_reason)。"""
    snap = ir_store.get_snapshot_full(sid, "IR3", int(ir3_version))
    if snap is None:
        return None, "ir3_snapshot_missing"
    if snap["invalidated_at"]:
        return None, "ir3_invalidated"
    content = snap.get("content")
    content = content if isinstance(content, dict) else {}
    script = content.get("cadquery_script")
    if not isinstance(script, str) or not script.strip():
        return None, "no_script"
    return script, None


def validate_combo(sid: str, combo: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """批准组合校验（语义照 api/cad.py C-1，扫描场景返回问题清单不抛异常）。

    返回 (snaps{IR类型→快照全量行}, problems)；problems 非空 = 组合不可信
    （快照缺失 / 已失效 / 与批准时哈希不符——content::text 口径）。
    """
    snaps: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for ir_type in ("IR2", "IR3", "IR4"):
        entry = combo.get(ir_type)
        if not isinstance(entry, dict):
            problems.append(f"{ir_type}_missing")
            continue
        snap = ir_store.get_snapshot_full(sid, ir_type, int(entry.get("version") or 0))
        if snap is None:
            problems.append(f"{ir_type}_v{entry.get('version')}_snapshot_missing")
            continue
        snaps[ir_type] = snap
        if snap["invalidated_at"]:
            problems.append(f"{ir_type}_v{snap['version']}_invalidated")
        expected = str(entry.get("content_sha256") or "")
        if expected:
            actual = hashlib.sha256((snap["content_text"] or "").encode("utf-8")).hexdigest()
            if actual != expected:
                problems.append(f"{ir_type}_hash_mismatch")
    return snaps, problems


def quality_gate(metadata: dict[str, Any]) -> tuple[bool, str | None]:
    """质量门：ir4_overall=fail 不可沉淀（例外批准也不行，ADR-015 §9 条 2）。"""
    policy = metadata.get("combo_policy") or {}
    overall = str(policy.get("ir4_overall") or "")
    if overall == "fail":
        return False, "ir4_fail"
    return True, None


def scan_candidates(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    """扫描可沉淀的 gate2 批准会话（廉价预检：不做沙箱重放——那只在执行时做）。

    返回 ``{"candidates": [...], "blocked": [...]}``：
    - candidates：通过判定链+质量门+脱敏检查的条目（depositable=True；已沉淀的
      ``already_deposited=True`` 并携带既有行——重复批准事件在此显形）；
    - blocked：组合无效（缺失/失效/哈希不符）/ ir4 fail / 无脚本 / secret 命中，
      每条带 reason 如实（A-16）。
    """
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for record in review_store.list_approved_gate2_records(limit=limit):
        sid = record["session_id"]
        metadata = record.get("metadata") or {}
        combo = metadata.get("snapshot_combo")
        base = {
            "session_id": sid,
            "record_id": record["id"],
            "session_title": record.get("session_title"),
            "decided_at": record.get("decided_at"),
            "reviewer_id": record.get("reviewer_id"),
        }
        if not isinstance(combo, dict) or not combo:
            blocked.append({**base, "reason": "no_combo"})
            continue
        _snaps, problems = validate_combo(sid, combo)
        if problems:
            blocked.append({**base, "reason": "combo_invalid", "detail": problems})
            continue
        ok, reason = quality_gate(metadata)
        if not ok:
            blocked.append({**base, "reason": reason})
            continue
        ir3_entry = combo.get("IR3") or {}
        ir3_version = int(ir3_entry.get("version") or 0)
        script, err = _script_of(sid, ir3_version)
        if script is None:
            blocked.append({**base, "reason": err})
            continue
        hits = _secret_scan(script)
        if hits:
            blocked.append({**base, "reason": "secret_hit", "detail": hits})
            continue
        sha = script_sha256(script)
        ref_id = derive_ref_id(sha)
        existing = ref_store.find_by_content(ref_id, sha)
        candidates.append({
            **base,
            "depositable": True,
            "ir3_version": ir3_version,
            "ir4_overall": str((metadata.get("combo_policy") or {}).get("ir4_overall") or ""),
            "script_sha256": sha,
            "ref_id": ref_id,
            "already_deposited": existing is not None,
            "existing_row": existing,
        })
    return {"candidates": candidates, "blocked": blocked}


# ------------------------------------------------------------------------------
# 沉淀执行（T16.3 WP2）：候选 → 重放/脱敏复核 → pending 行 + 凭证

class DepositError(Exception):
    """不可沉淀（判定链/质量门/重放/脱敏任一不过）——message 即人读原因（A-16）。"""

    def __init__(self, reason: str, detail: Any = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


def _deposit_inputs(sid: str, record_id: str) -> dict[str, Any]:
    """判定链+校验+质量门+取脚本（扫描与执行共用的前置链），失败抛 DepositError。"""
    record = review_store.get_review_record(sid, record_id)
    if record is None:
        raise DepositError("record_not_found")
    if record.get("gate") != "gate2" or record.get("decision") != "approved":
        raise DepositError("not_approved_gate2")
    metadata = record.get("metadata") or {}
    combo = metadata.get("snapshot_combo")
    if not isinstance(combo, dict) or not combo:
        raise DepositError("no_combo")
    snaps, problems = validate_combo(sid, combo)
    if problems:
        raise DepositError("combo_invalid", problems)
    ok, reason = quality_gate(metadata)
    if not ok:
        raise DepositError(reason or "ir4_fail")
    ir3_entry = combo.get("IR3") or {}
    ir3_version = int(ir3_entry.get("version") or 0)
    script, err = _script_of(sid, ir3_version)
    if script is None:
        raise DepositError(err or "no_script")
    hits = _secret_scan(script)
    if hits:
        raise DepositError("secret_hit", hits)
    return {
        "record_id": record_id,
        "combo": combo,
        "metadata": metadata,
        "ir3_version": ir3_version,
        "script": script,
        "snaps": snaps,
    }


def _session_title(sid: str) -> str | None:
    with review_store._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT title FROM design_sessions WHERE id = %s", (sid,))
        row = cur.fetchone()
    return row[0] if row else None


def _title_from_ir2(sid: str, snaps: dict[str, dict[str, Any]]) -> tuple[str, str | None]:
    """title/description 从 IR2 结构化生成（不做 AST 泛化——ADR-015 §9 条 4；确认前可 PATCH）。"""
    title = _session_title(sid) or "沉淀范例"
    ir2 = snaps.get("IR2") or {}
    content = ir2.get("content") if isinstance(ir2.get("content"), dict) else {}
    params = content.get("parameters") or []
    named = [f"{p.get('name')}={p.get('value')}{p.get('unit') or ''}"
             for p in params[:6] if isinstance(p, dict) and p.get("name")]
    desc = "；".join(named) if named else None
    return title[:120], desc


def deposit_from_record(sid: str, record_id: str, operator: str) -> dict[str, Any]:
    """执行沉淀：候选复核 → 真沙箱重放 → 桶上传 → pending 行 + 凭证（ADR-015 v1.1 §9）。

    幂等：同内容（同 ref_id 同 sha）已登记 → 返回 already=True + 既有行（不重复入库）。
    重放/质量/脱敏失败抛 DepositError（不落行——「不合格不入确认」的强实现）。
    insert 失败时已传对象残留可接受（幂等覆盖，重试安全——§9 条 10 清理边界）。
    """
    info = _deposit_inputs(sid, record_id)
    script = info["script"]
    sha = script_sha256(script)
    ref_id = derive_ref_id(sha)
    existing = ref_store.find_by_content(ref_id, sha)
    if existing is not None:
        logger.info("沉淀幂等命中：%s 已登记（v%s）", ref_id, existing["version"])
        return {"already": True, "row": existing, "ref_id": ref_id, "script_sha256": sha}

    # 真沙箱重放（子进程隔离——API 进程不加载 cadquery；失败不落行，A-16 如实）
    from cad_engine import sandbox

    replay = sandbox.run_in_subprocess(script, time_limit_s=REPLAY_TIME_LIMIT_S)
    if not replay.get("ok"):
        raise DepositError("replay_failed", str(replay.get("error") or "unknown"))

    title, desc = _title_from_ir2(sid, info["snaps"])
    combo_sha = str((info["combo"].get("IR3") or {}).get("content_sha256") or "")

    version = ref_store.next_version(ref_id)
    file_name = f"deposit_{sha[:8]}.py"
    from app_config import BUCKET_REF_SCRIPTS

    object_key = f"{ref_id}/v{version}/{file_name}"
    object_store.upload_text(script, BUCKET_REF_SCRIPTS, object_key)
    note = json.dumps({
        "kind": "deposit", "session_id": sid, "record_id": record_id,
        "ir3_snapshot_id": ((info["snaps"].get("IR3") or {}).get("id")),
        "ir3_version": info["ir3_version"],
        "combo_content_sha256": combo_sha,       # C-0 content::text 口径
        "script_sha256": sha,                    # 范例库 utf-8 口径（=content_sha256 列）
        "deposited_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "deposited_by": operator,
        "quality": {"ir4_overall": str((info["metadata"].get("combo_policy") or {}).get("ir4_overall") or ""),
                    "replay": "pass", "secret_scan": "pass"},
    }, ensure_ascii=False)
    try:
        row = ref_store.insert_row(
            ref_id=ref_id, version=version, file_name=file_name, title=title, description=desc,
            domain=None, domain_rules=None, sonar_types=[], keywords=[],
            bucket=BUCKET_REF_SCRIPTS, object_key=object_key, content_sha256=sha,
            status="pending", source_type="deposit", source_note=note, created_by=operator,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolation":
            # 并发沉淀同内容：唯一约束兜底 → 视为幂等命中
            again = ref_store.find_by_content(ref_id, sha)
            if again is not None:
                return {"already": True, "row": again, "ref_id": ref_id, "script_sha256": sha}
        raise
    logger.info("沉淀落库 %s v%s（pending，来源会话 %s）by %s", ref_id, version, sid, operator)
    return {"already": False, "row": row, "ref_id": ref_id, "script_sha256": sha}
