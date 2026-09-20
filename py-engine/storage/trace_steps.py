"""溯源链步骤构造（T2.4，纯函数，可独立单测）。

worker 落 IR 快照后调用对应 builder 构造链路行，再经 trace_store.record_steps 批量写入。
trace_id 沿用 A2 参数四件套（tr- 前缀）；A3 特征与 A4 问题继承关联参数的 trace_id；
A5 候选经 payload.source_trace_ids 继承（agent 侧负责回填）。
"""

from __future__ import annotations

from typing import Any


def _param_trace_map(ir2: dict[str, Any] | None) -> dict[str, str]:
    """IR2 参数 id → trace_id（A3/A4/A5 继承溯源的映射表）。"""
    return {
        p["id"]: p.get("trace_id", "")
        for p in (ir2 or {}).get("parameters", [])
        if p.get("id")
    }


def build_a2_steps(ir2: dict[str, Any], snapshot_id: str, session_id: str) -> list[dict[str, Any]]:
    """A2：逐参数 derive 行（参数诞生的源头步骤）。"""
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(ir2.get("parameters", []), 1):
        if not p.get("trace_id"):
            continue  # 无 trace 的参数本就进不了 IR2（A-06 断言），双保险
        rows.append({
            "trace_id": p["trace_id"],
            "step_no": i,
            "session_id": session_id,
            "actor": "A2",
            "action": "derive",
            "ir_snapshot_id": snapshot_id,
            "payload": {
                "param_id": p["id"], "name": p.get("name"),
                "source_type": p.get("source_type"),
                # 四件套其余三项一并入链（2026-09-20 用户反馈：A2 推导来源在溯源链中不可读）
                "source_ref": p.get("source_ref"),
                "derivation": p.get("derivation"),
                "confidence": p.get("confidence"),
                "value": p.get("value"), "unit": p.get("unit"),
            },
        })
    return rows


def build_a3_steps(ir3: dict[str, Any], ir2: dict[str, Any] | None, snapshot_id: str,
                   session_id: str, start_step: int = 1) -> list[dict[str, Any]]:
    """A3：逐特征 transform 行（trace 继承 linked_param_id 对应参数）。"""
    traces = _param_trace_map(ir2)
    rows: list[dict[str, Any]] = []
    step = start_step
    for f in ir3.get("features", []):
        trace_id = traces.get(f.get("linked_param_id"), "")
        if not trace_id:
            continue  # 悬空引用是 A4 的 error 项，溯源侧如实跳过
        rows.append({
            "trace_id": trace_id,
            "step_no": step,
            "session_id": session_id,
            "actor": "A3",
            "action": "transform",
            "ir_snapshot_id": snapshot_id,
            "payload": {
                "feature_id": f.get("feature_id"),
                "linked_param_id": f.get("linked_param_id"),
                "segment": f.get("segment"),
            },
        })
        step += 1
    return rows


def build_a4_steps(ir4: dict[str, Any], ir2: dict[str, Any] | None, snapshot_id: str,
                   session_id: str, start_step: int = 1) -> list[dict[str, Any]]:
    """A4：逐问题 validate 行（trace 取首个关联参数）。"""
    traces = _param_trace_map(ir2)
    rows: list[dict[str, Any]] = []
    step = start_step
    for issue in ir4.get("issues", []):
        related = issue.get("related_params", [])
        trace_id = traces.get(related[0], "") if related else ""
        if not trace_id:
            continue
        payload = {
            "constraint_id": issue.get("constraint_id"),
            "severity": issue.get("severity"),
            "message": issue.get("message"),
            "related_params": related,
        }
        if issue.get("suggestion"):
            payload["suggestion"] = issue["suggestion"]
        rows.append({
            "trace_id": trace_id,
            "step_no": step,
            "session_id": session_id,
            "actor": "A4",
            "action": "validate",
            "ir_snapshot_id": snapshot_id,
            "payload": payload,
        })
        step += 1
    return rows


def build_a5_steps(ir5: dict[str, Any], snapshot_id: str, session_id: str,
                   start_step: int = 1) -> list[dict[str, Any]]:
    """A5：逐候选 assign 行（trace 经 payload.source_trace_ids 继承）。"""
    rows: list[dict[str, Any]] = []
    step = start_step
    for i, cand in enumerate(ir5.get("candidates", [])):
        source_traces = (cand.get("payload") or {}).get("source_trace_ids") or []
        if not source_traces:
            continue
        rows.append({
            "trace_id": source_traces[0],
            "step_no": step,
            "session_id": session_id,
            "actor": "A5",
            "action": "assign",
            "ir_snapshot_id": snapshot_id,
            "payload": {"candidate_index": i, "type": cand.get("type")},
        })
        step += 1
    return rows
