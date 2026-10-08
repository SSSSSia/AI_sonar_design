"""公式依赖图（T14.7，外部审查问题 8 / 用例 17）——纯函数模块。

背景：E1 正向更新/提案应用此前只改目标参数，下游公式参数落库值不重算（改 n=16→32
后 DI 仍 12.04dB，A4 解析旧 derivation 与旧值比对照样 pass——过期值不暴露）。

解析口径（A-16 如实）：
- 依赖边 = formula 参数的注册公式符号 ← IR2 上游参数（参数名经 SYMBOL_ALIASES
  映射到符号，首个匹配；与 A2 取数同序）；
- kb_formulas 动态业务公式（registry 无命中）符号语义未登记 → 不建边、不自动重算
  （跳过并如实标注，不猜）；
- 图按 (session, IR2 快照) 现算，不持久化——参数表每版本重建，无需迁移表。
"""

from __future__ import annotations

from typing import Any

from solver import units
from solver.formulas import REGISTRY, SYMBOL_ALIASES
from solver.recompute import resolve_formula_key

MAX_CASCADE_DEPTH = 10  # 环保护（正常依赖图无环；脏数据兜底）


def resolve_param_symbol(param: dict[str, Any]) -> str | None:
    """参数名 → 公式符号（SYMBOL_ALIASES 精确匹配；无命中返回 None）。"""
    return SYMBOL_ALIASES.get(str(param.get("name") or ""))


def snapshot_inputs(params: list[dict[str, Any]], formula_param: dict[str, Any],
                    spec) -> dict[str, float] | None:
    """从 IR2 参数快照为 formula 参数解析公式输入（按符号别名匹配上游参数）。

    全部必需符号可解析 → 返回 {symbol: value}（已换算到公式单位）；
    任一必需符号无上游参数 → None（调用方回退 derivation 文本解析并标注）。
    """
    by_symbol: dict[str, dict[str, Any]] = {}
    for p in params:
        if p is formula_param:
            continue
        sym = resolve_param_symbol(p)
        if sym and sym not in by_symbol:
            by_symbol[sym] = p  # 首个匹配（与 A2 available_inputs 先到先得同序）
    inputs: dict[str, float] = {}
    for s in spec.symbols:
        up = by_symbol.get(s)
        if up is None:
            if s not in (spec.defaults or {}):
                return None  # 必需符号无快照来源
            continue
        try:
            inputs[s] = units.to_symbol_unit(up.get("value"), spec.symbol_unit(s))
        except units.UnresolvableQuantity:
            return None
    return inputs


def downstream_order(params: list[dict[str, Any]], changed_ids: list[str]) -> list[str]:
    """被改参数的下游 formula 参数（BFS 拓扑序，环保护+深度上限）。"""
    params = params or []
    by_id = {str(p.get("id")): p for p in params}
    graph: dict[str, list[str]] = {}
    for p in params:
        if p.get("source_type") != "formula":
            continue
        key = resolve_formula_key(p.get("source_ref", ""))
        if key is None:
            continue  # db 动态公式不建边（口径见模块 docstring）
        spec = REGISTRY[key]
        ups: list[str] = []
        for q in params:
            if q is p:
                continue
            sym = resolve_param_symbol(q)
            if sym and sym in spec.symbols:
                ups.append(str(q.get("id")))
        graph[str(p.get("id"))] = ups

    order: list[str] = []
    seen: set[str] = set(changed_ids)
    frontier = list(changed_ids)
    depth = 0
    while frontier and depth < MAX_CASCADE_DEPTH:
        depth += 1
        nxt: list[str] = []
        for pid in graph_order(graph, by_id):
            if pid in seen:
                continue
            if any(u in frontier for u in graph.get(pid, [])):
                order.append(pid)
                seen.add(pid)
                nxt.append(pid)
        frontier = nxt
    return order


def graph_order(graph: dict[str, list[str]], by_id: dict[str, dict[str, Any]]) -> list[str]:
    """确定性遍历序：按 IR2 参数表顺序（依赖重算的留痕顺序可复现）。"""
    return [pid for pid in by_id if pid in graph]
