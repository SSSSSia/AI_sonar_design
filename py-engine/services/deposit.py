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

import hashlib
import re
from typing import Any

from storage import ir_store, ref_store, review_store

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
