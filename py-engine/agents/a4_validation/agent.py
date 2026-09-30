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
  数值复核  → linked 参数 vs 实测 bbox 维度（T7.4，PHASE-007；容差可配默认 2%，无 LLM）

三级风险：error=高（须回环）/ warning=中（来源冲突存疑）/ info=低（提示）。
overall：存在任一 error 即 fail。输出经 contracts/ir/ir4.schema.json 契约校验（A-04）。
"""

from __future__ import annotations

import logging
from typing import Any

from agents.a3_cad.geometry_link import UNIT_TO_MM
from app_config import CAD_GEO_NUM_TOL_PCT
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

        # CSR 统计器（T9.2，PHASE-009）：口径写死于本注释与 PHASE-009 §2.2，前端 tooltip 同文案——
        # CSR = 通过的约束检查数 / (通过 + 违反) × 100。
        # 计数对象 = 产出确定性判定的检查：formula 复算、standard 符合性核对、几何数值复核（CHK-GEO-NUM）；
        # warning（来源冲突存疑）与 info（提示/待补/跳过）不计入分母——存疑不等于违反，避免虚低；
        # 结构完整性错误（引用悬空/特征重复/产物缺失）属产物完整性而非约束满足，由 overall=fail 体现，不计入 CSR；
        # 无任何有效检查（全 skip / 空方案）→ csr=None（A-16 无数据不造假，前端显示「—」）。
        stats: dict[str, int] = {"pass": 0, "violation": 0}

        issues: list[dict[str, Any]] = []
        issues += self._check_formula(params, stats)
        issues += self._check_case(params)
        issues += self._check_standard(params, stats)
        issues += self._check_estimated(params)
        issues += self._check_geometry(ir2, ir3 or {}, stats)

        total = stats["pass"] + stats["violation"]
        csr = round(stats["pass"] / total * 100, 2) if total > 0 else None
        ir4 = {
            "overall": "fail" if any(i["severity"] == "error" for i in issues) else "pass",
            "issues": issues,
            "csr": csr,
        }
        return validate_ir("IR4", ir4)

    # ------------------------------------------------------------------
    def _check_formula(self, params: list[dict[str, Any]], stats: dict[str, int]) -> list[dict[str, Any]]:
        """formula 参数：SymPy 复算。定位顺序：solver REGISTRY → kb_formulas 业务库（T3.3）；
        都无法定位的出 info 请人工复核，不硬失败。"""
        issues: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") != "formula":
                continue
            key = recompute.resolve_formula_key(p.get("source_ref", ""))
            db_row = None
            if key is None:
                db_row = self._match_db_formula(p)
                if db_row is None:
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
                if key is not None:
                    result = recompute.recompute(key, inputs)
                else:
                    symbols = db_row.get("symbols") or []
                    result = recompute.recompute_dynamic(
                        expr=db_row["expr"], symbols=symbols, defaults={},
                        inputs=inputs, formula_name=db_row["name"], unit=db_row.get("unit", "-"),
                        formula_key=f"kb_formulas:{db_row['key']}",
                    )
            except KeyError as exc:
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "info",
                    f"formula 参数「{p.get('name')}」复算输入不全（{exc}），请人工复核",
                    [p["id"]]))
                continue
            value = _first_number(p.get("value"))
            if value is None:
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"formula 参数「{p.get('name')}」取值缺失或非数值，无法与复算结果比对",
                    [p["id"]], suggestion="back_to_A2"))
            elif not recompute.values_match(result.value, value):
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-FORMULA-RECOMPUTE", "error",
                    f"复算不符：参数「{p.get('name')}」登记值 {p.get('value')} {p.get('unit')}，"
                    f"{result.derivation}（依据 {p.get('source_ref')}）",
                    [p["id"]], suggestion="back_to_A2"))
            else:
                stats["pass"] += 1  # 复算一致：静默通过（不产 issue），计入 CSR
        return issues

    @staticmethod
    def _match_db_formula(p: dict[str, Any]) -> dict[str, Any] | None:
        """业务库 kb_formulas 定位（T3.3）：key 出现在 source_ref 或名称一致；库不可达→None。"""
        try:
            from storage import registry_store

            for row in registry_store.list_rows_cached("formulas"):
                if row.get("key") and str(row["key"]) in str(p.get("source_ref") or ""):
                    return row
                if row.get("name") and str(row["name"]) == str(p.get("name")):
                    return row
        except Exception:
            return None
        return None

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
    def _check_standard(self, params: list[dict[str, Any]], stats: dict[str, int]) -> list[dict[str, Any]]:
        """standard 参数：军标核对（T9.3：库内 expr 条目自动求值；A-16 空库/未覆盖如实待补）。"""
        issues: list[dict[str, Any]] = []
        for p in params:
            if p.get("source_type") != "standard":
                continue
            result = standards.check_param(p, all_params=params)
            if result["status"] == "violation":
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-STD-VIOLATION", "error",
                    f"军标违例：{result['message']}", [p["id"]], suggestion="back_to_A2"))
            elif result["status"] == "pass":
                stats["pass"] += 1  # 军标核对通过：静默（不产 issue），计入 CSR
            elif result["status"] == "uncovered":
                # 条目引用符号在 IR2 无对应值：如实提示未覆盖，不算违反（A-16）
                issues.append(_issue(
                    "CHK-STD-UNCOVERED", "info",
                    f"参数「{p.get('name')}」：{result['message']}", [p["id"]]))
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
    def _check_geometry(self, ir2: dict[str, Any], ir3: dict[str, Any],
                        stats: dict[str, int]) -> list[dict[str, Any]]:
        """几何自洽：特征-参数引用完整、feature_id 唯一、STEP/脚本引用非空
        ＋ 数值级复核（T7.4：linked 参数 vs 实测 bbox 维度）。"""
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
            if linked is not None and linked not in param_ids:
                # null=实测未联动，合法（T7.1 契约放宽，A-16）；非 null 才查悬空引用
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

        issues += self._check_geometry_numeric(ir2, ir3, stats)
        return issues

    # ------------------------------------------------------------------
    def _check_geometry_numeric(self, ir2: dict[str, Any], ir3: dict[str, Any],
                                stats: dict[str, int]) -> list[dict[str, Any]]:
        """数值级几何复核（T7.4，PHASE-007）。确定性无 LLM；A-09 只报告不改参数。

        - extracted：逐个「已联动」特征比对 bbox[linked_dim] vs 参数值（换算 mm），
          相对偏差 ≤ CAD_GEO_NUM_TOL_PCT（默认 2%，对齐 mockup 06 口径）→ info；超容差 → error；
        - 退化几何（part_count=0 或体积 ≤0）→ CHK-GEO-DEGENERATE error；
        - 提取失败 / 无特征 / 旧版本快照（features 无实测数值）→ CHK-GEO-NUM-SKIP info，
          如实说明未执行数值复核，不阻断（A-16）。
        """
        issues: list[dict[str, Any]] = []
        summary = ir3.get("geometry_summary") or {}
        source = summary.get("source")
        features: list[dict[str, Any]] = ir3.get("features", [])
        params_by_id = {p["id"]: p for p in ir2.get("parameters", [])}

        if source != "extracted":
            if source == "extraction_failed":
                detail = str(summary.get("detail") or "")
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "info",
                    f"几何数值复核未执行：STEP 实测提取未成功{'（' + detail + '）' if detail else ''}，"
                    "本轮仅完成引用完整性校验"))
            elif not features:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "info",
                    "IR3 无特征（降级产物），几何数值复核未执行，仅完成引用完整性校验"))
            else:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "info",
                    "旧版本快照无数值几何（features 未携带实测 bbox），仅完成引用完整性校验"))
            return issues

        # 退化几何
        part_count = summary.get("part_count")
        volume = summary.get("volume_mm3")
        if part_count == 0 or (isinstance(volume, (int, float)) and volume <= 0):
            issues.append(_issue(
                "CHK-GEO-DEGENERATE", "error",
                f"几何提取结果退化（part_count={part_count}，volume_mm3={volume}），模型可能为空或非实体",
                [], [], suggestion="back_to_A3"))

        # 数值比对（只对「已联动」的特征；A-09：只报告）
        tol = CAD_GEO_NUM_TOL_PCT
        linked_any = False
        for f in features:
            fid = f.get("feature_id", "?")
            linked = f.get("linked_param_id")
            if not linked:
                continue
            linked_any = True
            p = params_by_id.get(linked)
            geometry = f.get("geometry") or {}
            bbox = geometry.get("bbox_mm")
            dim = geometry.get("linked_dim")
            if p is None or not isinstance(bbox, list) or dim not in (0, 1, 2) or len(bbox) <= dim:
                continue  # 悬空引用已由引用完整性检查覆盖；缺数值按旧快照口径由上方兜底提示
            factor = UNIT_TO_MM.get(str(p.get("unit", "")).strip().lower())
            value = _first_number(p.get("value"))
            if factor is None or value is None or value <= 0:
                issues.append(_issue(
                    "CHK-GEO-NUM-SKIP", "info",
                    f"参数 {linked}「{p.get('name')}」单位/取值非长度数值，未做数值复核",
                    [linked], [fid]))
                continue
            expected_mm = value * factor
            measured = float(bbox[dim])
            dev_pct = abs(measured - expected_mm) / expected_mm * 100.0
            if dev_pct <= tol:
                stats["pass"] += 1  # 数值一致（issue 保持 info 级降低噪音），计入 CSR
                issues.append(_issue(
                    "CHK-GEO-NUM", "info",
                    f"数值一致：参数 {linked}「{p.get('name')}」= {value}{p.get('unit')}，"
                    f"关联特征 {fid} 实测 {measured:g} mm，偏差 {dev_pct:.2f}%（容差 {tol:g}%）",
                    [linked], [fid]))
            else:
                stats["violation"] += 1
                issues.append(_issue(
                    "CHK-GEO-NUM", "error",
                    f"{linked}「{p.get('name')}」= {value}{p.get('unit')}，但关联特征 {fid} "
                    f"实测 {measured:g} mm，偏差 {dev_pct:.1f}%（容差 {tol:g}%），"
                    "建议在审阅关结合 IR4 裁决",
                    [linked], [fid], suggestion="back_to_A3"))
        if not linked_any:
            issues.append(_issue(
                "CHK-GEO-NUM-SKIP", "info",
                "几何为实测提取，但无已联动的长度类参数可做数值复核（联动容差内无匹配）"))
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
