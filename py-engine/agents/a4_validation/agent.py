"""A4 合规校验 Agent（T2.1，IR2 + IR3 → IR4）。

模块边界（宪法 §3.1 / A-09）：**只出报告不改参数**；不通过由编排层触发局部回环（A-11）。
**纯确定性实现，不调用 LLM**——合规校验必须可审计、可复现（A-07 精神；llm 参数仅为与其他
Agent 管线签名一致而保留，未使用）。

按 source_type 分流校验 + 几何自洽：
  formula  → solver.recompute：SymPy 精确代入复算（derivation 解析代入值，容差比对）
  case     → solver.intervals：候选池区间传播 + 同符号候选冲突检测
  standard → solver.standards：军标核对（空库如实"待补"，A-16）
  estimated → 汇总 info：未经公式验证，请人工复核
  几何自洽  → features.linked_param_id ⊆ IR2 参数 id / feature_id 唯一 / step·script 引用完整

三级风险：error=高（须回环）/ warning=中（来源冲突存疑）/ info=低（提示）。
overall：存在任一 error 即 fail。输出经 contracts/ir/ir4.schema.json 契约校验（A-04）。
"""

from __future__ import annotations

import logging
from typing import Any

from ir_contract import validate_ir
from solver import intervals, recompute, standards
from solver.formulas import REGISTRY, _first_number
from solver.recompute import ABS_TOL

logger = logging.getLogger("sonarcad.a4")


class A4Agent:
    """A4 合规校验。不依赖 LLM 与网络（standards/neo4j 之外的纯内存计算）。"""

    def __init__(self, llm: Any = None):  # noqa: ARG002 - 管线签名一致性，A4 确定性不用 LLM
        self._llm = llm

    # ------------------------------------------------------------------
    def run(self, ir2: dict[str, Any], ir3: dict[str, Any] | None = None) -> dict[str, Any]:
        params: list[dict[str, Any]] = ir2.get("parameters", [])
        features: list[dict[str, Any]] = (ir3 or {}).get("features", [])

        issues: list[dict[str, Any]] = []
        issues += self._check_formula(params)
        issues += self._check_case(params)
        issues += self._check_standard(params)
        issues += self._check_estimated(params)
        issues += self._check_geometry(ir2, ir3 or {})

        ir4 = {
            "overall": "fail" if any(i["severity"] == "error" for i in issues) else "pass",
            "issues": issues,
        }
        return validate_ir("IR4", ir4)

    # ------------------------------------------------------------------
    def _check_formula(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """formula 参数：SymPy 复算（无法定位/解析的出 info 请人工复核，不硬失败）。"""
        issues: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") != "formula":
                continue
            key = recompute.resolve_formula_key(p.get("source_ref", ""))
            if key is None:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "info",
                    f"formula 参数「{p.get('name')}」无法定位注册公式"
                    f"（source_ref={p.get('source_ref')}），未复算，请人工复核",
                    [p["id"]]))
                continue
            inputs = recompute.parse_inputs_from_derivation(p.get("derivation", ""))
            if not inputs:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "info",
                    f"formula 参数「{p.get('name')}」的 derivation 未记录代入值，无法复算，请人工复核",
                    [p["id"]]))
                continue
            try:
                result = recompute.recompute(key, inputs)
            except KeyError as exc:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "info",
                    f"formula 参数「{p.get('name')}」复算输入不全（{exc}），请人工复核",
                    [p["id"]]))
                continue
            value = _first_number(p.get("value"))
            if value is None:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"formula 参数「{p.get('name')}」取值缺失或非数值，无法与复算结果比对",
                    [p["id"]], suggestion="back_to_A2"))
            elif not recompute.values_match(result.value, value):
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"复算不符：参数「{p.get('name')}」登记值 {p.get('value')} {p.get('unit')}，"
                    f"{result.derivation}（依据 {p.get('source_ref')}）",
                    [p["id"]], suggestion="back_to_A2"))
        return issues

    # ------------------------------------------------------------------
    def _check_case(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """case 参数：区间传播（采纳值与案例候选总体相容性）+ 候选冲突检测。"""
        issues: list[dict[str, Any]] = []
        pools = intervals.candidate_pools(params)
        formula_by_key: dict[str, list[dict[str, Any]]] = {}
        for p in params:
            if p.get("source_type") != "formula":
                continue
            key = recompute.resolve_formula_key(p.get("source_ref", ""))
            if key:
                formula_by_key.setdefault(key, []).append(p)

        for key in REGISTRY:
            interval = intervals.propagate_interval(key, pools)
            if interval is None:
                continue
            low, high = interval
            for p in formula_by_key.get(key, []):
                value = _first_number(p.get("value"))
                if value is None:
                    continue
                eps = 1e-6 * max(1.0, abs(value))
                if value < low - eps or value > high + eps:
                    issues.append(_issue(
                        "CHK-CASE-INTERVAL", "warning",
                        f"参数「{p.get('name')}」采纳值 {value} 落在案例候选传播区间 "
                        f"[{low:.6g}, {high:.6g}] 之外（{p.get('source_ref')}）——"
                        "与其他案例来源不一致，采纳值可能掩盖了候选间的取舍",
                        [p["id"]]))

        for c in intervals.case_conflicts(pools):
            issues.append(_issue(
                "CHK-CASE-CONFLICT", "warning",
                f"符号「{c['symbol']}」存在相互冲突的案例候选：{c['values']}"
                f"（参数 {', '.join(c['param_ids'])}），请人工裁决取舍",
                c["param_ids"]))
        return issues

    # ------------------------------------------------------------------
    def _check_standard(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """standard 参数：军标核对（A-16：空库如实待补，不伪装已核对）。"""
        issues: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") != "standard":
                continue
            result = standards.check_param(p)
            if result["status"] == "violation":
                issues.append(_issue(
                    "CHK-STD-VIOLATION", "error",
                    f"军标违例：{result['message']}", [p["id"]], suggestion="back_to_A2"))
            elif result["status"] == "pending":
                issues.append(_issue(
                    "CHK-STD-PENDING", "info",
                    f"参数「{p.get('name')}」：{result['message']}", [p["id"]]))
        return issues

    # ------------------------------------------------------------------
    def _check_estimated(self, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        estimated = [p for p in params if p.get("source_type") == "estimated"]
        if not estimated:
            return []
        names = "、".join(p.get("name", "?") for p in estimated)
        return [_issue(
            "CHK-EST-UNVERIFIED", "info",
            f"{len(estimated)} 个估算参数未经公式验证，请人工复核：{names}",
            [p["id"] for p in estimated])]

    # ------------------------------------------------------------------
    def _check_geometry(self, ir2: dict[str, Any], ir3: dict[str, Any]) -> list[dict[str, Any]]:
        """几何自洽：特征-参数引用完整、feature_id 唯一、STEP/脚本引用非空。"""
        issues: list[dict[str, Any]] = []
        param_ids = {p["id"] for p in ir2.get("parameters", [])}
        features: list[dict[str, Any]] = ir3.get("features", [])

        seen: set[str] = set()
        for f in features:
            fid = f.get("feature_id", "?")
            if fid in seen:
                issues.append(_issue(
                    "CHK-GEO-DUP-FEATURE", "error",
                    f"特征 id 重复：{fid}", [], [fid], suggestion="back_to_A3"))
            seen.add(fid)
            linked = f.get("linked_param_id")
            if linked not in param_ids:
                issues.append(_issue(
                    "CHK-GEO-LINK", "error",
                    f"特征 {fid} 引用了 IR2 中不存在的参数 {linked}（悬空引用）",
                    [linked] if linked else [], [fid], suggestion="back_to_A3"))

        step_ref = ir3.get("step_file") or {}
        if not step_ref.get("bucket") or not step_ref.get("object_key"):
            issues.append(_issue(
                "CHK-GEO-STEP", "error",
                "IR3 缺少有效的 STEP 产物引用（bucket/object_key 为空）",
                [], [], suggestion="back_to_A3"))
        if not (ir3.get("cadquery_script") or "").strip():
            issues.append(_issue(
                "CHK-GEO-SCRIPT", "error",
                "IR3 缺少 CadQuery 脚本",
                [], [], suggestion="back_to_A3"))
        return issues


def _issue(
    constraint_id: str,
    severity: str,
    message: str,
    related_params: list[str] | None = None,
    related_features: list[str] | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "constraint_id": constraint_id,
        "severity": severity,
        "message": message,
        "related_params": related_params or [],
        "related_features": related_features or [],
    }
    if suggestion:
        issue["suggestion"] = suggestion
    return issue


# ABS_TOL 从 recompute 再导出供测试引用（容差口径单点定义）
__all__ = ["A4Agent", "ABS_TOL"]
