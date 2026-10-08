"""长度类参数 ↔ 实测分段自动联动（T7.3，PHASE-007）。

纯函数模块（无 IO / 无 LLM——A-07 精神：数值判断交给代码，确定性可单测）。
规则（PHASE-007 §2.4）：
- 候选参数：unit ∈ {mm, cm, dm, m}（长度类），取值换算为 mm；
- 匹配：参数值 vs 分段 bbox 三维的最佳相对偏差 ≤ tol_pct（默认 20%，粗匹配——
  误配后果仅为多一条 info/error，人工 gate2 可见可裁）；
- 一一对应：一参多段命中取偏差最小段，一段只联一参（全局按偏差最小者贪心）；
- 未命中 → linked_param_id=null，如实（A-16 禁止造假联动）。
"""

from __future__ import annotations

from typing import Any

from solver.formulas import _first_number
from solver.units import LENGTH_TO_MM as UNIT_TO_MM  # T14.3：长度族因子收口单点维护


def length_params_mm(params: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    """长度类数值参数 → [(param, value_mm)]；非长度单位/非数值/非正值的如实跳过。"""
    out: list[tuple[dict[str, Any], float]] = []
    for p in params:
        factor = UNIT_TO_MM.get(str(p.get("unit", "")).strip().lower())
        if factor is None:
            continue
        value = _first_number(p.get("value"))
        if value is None or value <= 0:
            continue
        out.append((p, value * factor))
    return out


def link_params(
    params: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    tol_pct: float,
) -> dict[int, dict[str, Any]]:
    """IR2 参数 × 实测分段 → {part_index: {"param_id", "dim"}}（一一对应贪心匹配）。

    tol_pct ≤ 0 视为关闭联动（返回空，所有段如实 null）。
    """
    if tol_pct <= 0:
        return {}
    candidates: list[tuple[float, str, int, int]] = []
    for p, v_mm in length_params_mm(params):
        for part in parts:
            for dim, size in enumerate(part["bbox_mm"]):
                if size <= 0:
                    continue
                dev_pct = abs(size - v_mm) / v_mm * 100.0
                if dev_pct <= tol_pct:
                    candidates.append((dev_pct, str(p["id"]), int(part["index"]), dim))
    candidates.sort()
    used_params: set[str] = set()
    used_parts: set[int] = set()
    links: dict[int, dict[str, Any]] = {}
    for _dev, pid, pidx, dim in candidates:
        if pid in used_params or pidx in used_parts:
            continue
        used_params.add(pid)
        used_parts.add(pidx)
        links[pidx] = {"param_id": pid, "dim": dim}
    return links
