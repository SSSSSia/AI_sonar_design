"""case 参数区间传播校验（T2.1）。

case（案例）参数来自知识库检索的经验值，同一物理量可能有多个候选来源。
本模块把候选值按公式符号归拢，对注册公式做候选组合的区间传播，
供 A4 检查 IR2 实际采纳的 formula 值是否与全部案例候选相容（A-16：冲突必须让人看见）。
"""

from __future__ import annotations

from itertools import product
from typing import Any

from solver import units
from solver.formulas import REGISTRY, SYMBOL_ALIASES

# 组合爆炸保护：候选组合超过该值时按符号均匀截断
MAX_COMBOS = 500

_ALL_SYMBOLS = {s for spec in REGISTRY.values() for s in spec.symbols}


def candidate_pools(params: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """从 IR2 参数表中归拢 case/estimated 数值候选：符号 -> [{value, param_id, source_type}]。

    formula/standard 参数不进候选池（formula 是被校验对象，standard 不提供数值）。
    T14.3 单位感知：值带单位按符号登记单位换算；不可解析/量纲冲突剔除（宁缺勿错）。
    """
    pools: dict[str, list[dict[str, Any]]] = {}
    for p in params or []:
        if p.get("source_type") not in ("case", "estimated"):
            continue
        symbol = SYMBOL_ALIASES.get(p.get("name") or "", p.get("name"))
        if symbol not in _ALL_SYMBOLS:
            continue
        spec = next((sp for sp in REGISTRY.values() if symbol in sp.symbols), None)
        try:
            value = units.to_symbol_unit(p.get("value"), spec.symbol_unit(symbol) if spec else "")
        except units.UnresolvableQuantity:
            continue
        pools.setdefault(symbol, []).append({
            "value": value,
            "param_id": p.get("id"),
            "source_type": p.get("source_type"),
        })
    return pools


def propagate_interval(formula_key: str, pools: dict[str, list[dict[str, Any]]]) -> tuple[float, float] | None:
    """对一条公式做候选组合的区间传播，返回 (min, max)；必需符号无候选时返回 None。

    默认值符号（如 c=1500）若无候选则取 defaults。
    """
    spec = REGISTRY[formula_key]
    needed = spec.required_symbols()
    if any(s not in pools for s in needed):
        return None
    options: list[list[float]] = []
    for s in needed:
        values = [c["value"] for c in pools[s]]
        total = 1
        for o in options:
            total *= len(o)
        cap = max(1, MAX_COMBOS // max(total, 1))
        options.append(values[:cap])

    results: list[float] = []
    for combo in product(*options):
        kwargs = dict(zip(needed, combo))
        kwargs.update({s: v for s, v in (spec.defaults or {}).items() if s not in kwargs})
        try:
            results.append(float(spec.compute(**kwargs)))
        except Exception:
            continue
    if not results:
        return None
    return min(results), max(results)


def case_conflicts(
    pools: dict[str, list[dict[str, Any]]], rel_tol: float = 0.10
) -> list[dict[str, Any]]:
    """同一符号多个候选值差异超过 rel_tol 视为冲突（来源间不一致，须人工裁决）。"""
    conflicts: list[dict[str, Any]] = []
    for symbol, cands in pools.items():
        values = [c["value"] for c in cands]
        if len(values) < 2:
            continue
        low, high = min(values), max(values)
        if low <= 0:
            continue  # 区间比较仅对正值物理量有意义
        if (high - low) > rel_tol * low:
            conflicts.append({
                "symbol": symbol,
                "values": values,
                "param_ids": [c["param_id"] for c in cands],
            })
    return conflicts
