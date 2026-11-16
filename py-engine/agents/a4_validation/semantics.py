# -*- coding: utf-8 -*-
"""A4 语义检查器注册接口与检查器（T15.3 首批 / T15.5 壁厚，PHASE-015 / ADR-014 §4、§7）。

按尺寸语义分检查器（审查问题 3/7 的"按尺寸语义分检查器"落点）：
- 注册接口：``register_checker(dim_type, fn)``——新尺寸语义（跨度等）注册即用，
  A4 主流程不因新增维度改动（B-4b 扩展点）；
- 检查器：length（CHK-SEM-LENGTH）/ diameter（CHK-SEM-DIAMETER）/
  thickness（CHK-SEM-THICKNESS，T15.5 壁厚规则族第一条，实测=wall_derived 证据）；
- 取数：IR2 参数 value×单位因子（solver/units 收口单源，T14.3），期望 mm；
- 容差：解析链 **按参数登记（PARAM_TOL_PCT，T15.5）> 按 dim_type 覆盖（DIM_TOL_PCT）
  > 全局默认 app_config.SEMANTIC_TOL_PCT**（对齐 CAD_GEO_NUM_TOL_PCT=2% 口径）；
  壁厚不设 dim 级缺省——2% 对 3mm 壁厚（±0.06mm）过紧是真实约束，由按参数登记
  显式放宽而非静默全局放宽（A-16，ADR-014 §7）；
- 诚实语义（A-16/A-09）：无可靠测量（measured_mm=None / measurement=none）、单位非长度、
  参数缺失 → unverified（应检未检显性），绝不以猜测凑 pass/violation；
  孔径测量只认 radius_derived 圆度证据（T15.2 已在映射层保证，检查器再校验一次）。

检查器为纯函数：输入 (param: IR2 参数条目, entry: IR3 mapped_param 条目, tol_pct)，
输出 {verdict: pass|violation|unverified, measured_mm, expected_mm, deviation_pct, reason}。
verdict→严重级映射在 A4 主流程（pass→info / violation→error+back_to_A3 / unverified→unverified）。
"""

from __future__ import annotations

from typing import Any, Callable

from agents.a3_cad.geometry_link import UNIT_TO_MM
from solver.formulas import _first_number

# 检查器注册表：dim_type → 检查器函数（T15.3 首批 length/diameter）
SEMANTIC_CHECKERS: dict[str, Callable[[dict[str, Any], dict[str, Any], float], dict[str, Any]]] = {}

# 按 dim_type 的容差覆盖（None=用全局默认 SEMANTIC_TOL_PCT）
# 壁厚不设缺省（ADR-014 §7）：2% 对 3mm 壁厚过紧是真实约束，由按参数登记显式放宽。
DIM_TOL_PCT: dict[str, float | None] = {"length": None, "diameter": None, "thickness": None}

# 按参数登记容差（T15.5，ADR-014 §7）：param_id → tol_pct，优先级最高。
# 登记=显式策略行为（操作员/未来规则包写入），A4 报告须披露出处（tolerance_source）。
PARAM_TOL_PCT: dict[str, float] = {}


def register_checker(
    dim_type: str,
    fn: Callable[[dict[str, Any], dict[str, Any], float], dict[str, Any]],
) -> None:
    """注册尺寸语义检查器（B-4b 扩展点）：dim_type 须与 IR3 mapped_param.dim_type 同枚举。"""
    SEMANTIC_CHECKERS[dim_type] = fn


def register_param_tolerance(param_id: str, tol_pct: float) -> None:
    """按参数登记容差（B-4b/T15.5）：param_id → tol_pct，优先级高于 dim_type/全局。

    登记是显式策略行为（如壁厚 3mm 按 ±10% 放宽）；A4 issue 消息披露出处（A-16）。
    """
    PARAM_TOL_PCT[str(param_id)] = float(tol_pct)


def resolve_tolerance(dim_type: str, default_pct: float,
                      param_id: str | None = None) -> float:
    """容差解析：按参数登记 > 按 dim_type 覆盖 > 全局默认（T15.5 加参数层）。"""
    if param_id is not None:
        p = PARAM_TOL_PCT.get(str(param_id))
        if p is not None:
            return float(p)
    override = DIM_TOL_PCT.get(dim_type)
    return float(override) if override is not None else float(default_pct)


def tolerance_source(dim_type: str, param_id: str | None = None) -> str:
    """容差来源标签（A-16：报告披露出处）：param=按参数登记 / dim=dim_type 缺省 / global=全局默认。"""
    if param_id is not None and str(param_id) in PARAM_TOL_PCT:
        return "param"
    if DIM_TOL_PCT.get(dim_type) is not None:
        return "dim"
    return "global"


def _check_dimension(param: dict[str, Any], entry: dict[str, Any], tol_pct: float) -> dict[str, Any]:
    """长度族尺寸检查（length/diameter 共用逻辑，label 仅用于文案）。"""
    value = _first_number(param.get("value"))
    factor = UNIT_TO_MM.get(str(param.get("unit", "")).strip().lower())
    measured = entry.get("measured_mm")
    if value is None or value <= 0 or factor is None:
        return {"verdict": "unverified", "measured_mm": measured, "expected_mm": None,
                "deviation_pct": None, "reason": "参数取值/单位非长度数值，无法确定期望值（如实未检）"}
    if measured is None:
        return {"verdict": "unverified", "measured_mm": None, "expected_mm": float(value * factor),
                "deviation_pct": None, "reason": "映射无可靠实测证据（measurement=none），如实未检"}
    expected = float(value) * factor
    deviation = abs(float(measured) - expected) / expected * 100.0
    return {
        "verdict": "pass" if deviation <= tol_pct else "violation",
        "measured_mm": float(measured),
        "expected_mm": expected,
        "deviation_pct": deviation,
        "reason": "",
    }


# 检查器（同一实现，容差/CHK-ID 按 dim_type 分：孔径未来可独立收紧容差；
# 壁厚 T15.5 注册即用，实测=wall_derived 证据（ADR-014 §7），无证据如实 unverified）
register_checker("length", _check_dimension)
register_checker("diameter", _check_dimension)
register_checker("thickness", _check_dimension)


def covered_param_ids(ir3: dict[str, Any]) -> set[str]:
    """IR3 param_mapping 覆盖的参数 id 集合（含 verified/ambiguous/unmatched 全部声明）——
    这些参数由映射链路负责核对，legacy CHK-GEO-NUM 跳过以避免双重裁决与 coverage 重复计数。"""
    mapping = ir3.get("param_mapping") or {}
    out: set[str] = set()
    for obj in mapping.get("objects", []):
        for p in obj.get("params", []):
            if p.get("param_id"):
                out.add(str(p["param_id"]))
    return out
