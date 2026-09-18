"""SymPy 精确代入复算底座（T2.1，D-15）。

A4 合规校验对 source_type=formula 的参数做等式复算：
从参数 derivation 解析代入值（"n=16, …" 约定，由 formulas.evaluate_available 固化），
按注册公式的 SymPy 表达式精确代入求值，与参数值容差比对。

与 formulas.evaluate_available（math 闭包，A2 生成路径）互为对照：同输入必须同输出。
source_ref 无 registry 命中时返回 None（A4 出 info 提示人工复核，不硬失败——注册表会增长）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sympy

from solver.formulas import REGISTRY, FormulaSpec

# evaluate_available 落库时对值做 round(4)，故复算比对放宽到该量级的绝对误差
ABS_TOL = 1e-4
REL_TOL = 1e-6

_DERIV_INPUT = re.compile(r"([a-z][a-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class RecomputeResult:
    """一次复算结果。derivation 为人读复算过程（含代入值，可直接审计）。"""

    formula_key: str
    value: float
    unit: str
    inputs: dict[str, float]
    derivation: str


def parse_inputs_from_derivation(derivation: str) -> dict[str, float]:
    """从 derivation 提取 "符号=数值" 对（如 "n=16, c=1500"）。"""
    out: dict[str, float] = {}
    for m in _DERIV_INPUT.finditer(derivation or ""):
        out.setdefault(m.group(1), float(m.group(2)))
    return out


def resolve_formula_key(source_ref: str) -> str | None:
    """按 source_ref 的 '#' 尾段（如 "DI≈10lg(N)"）在注册表中定位公式。"""
    for key, spec in REGISTRY.items():
        tail = spec.source_ref.split("#")[-1]
        if tail and tail in (source_ref or ""):
            return key
    return None


def values_match(a: float, b: float, rel: float = REL_TOL, abs_: float = ABS_TOL) -> bool:
    """round(4) 兼容的容差比对：绝对或相对误差任一达标即认为一致。"""
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def recompute(formula_key: str, inputs: dict[str, float]) -> RecomputeResult:
    """SymPy 精确代入。缺必需符号抛 KeyError；结果与 compute 闭包同值（单测互证）。"""
    spec: FormulaSpec = REGISTRY[formula_key]
    return recompute_dynamic(
        expr=spec.expr,
        symbols=spec.symbols,
        defaults=spec.defaults or {},
        inputs=inputs,
        formula_name=spec.name,
        unit=spec.unit,
        formula_key=formula_key,
    )


def recompute_dynamic(
    expr: str,
    symbols: dict[str, str] | list[str],
    defaults: dict[str, float],
    inputs: dict[str, float],
    formula_name: str,
    unit: str,
    formula_key: str = "dynamic",
) -> RecomputeResult:
    """对任意 SymPy 表达式做精确代入（T3.3：支持 kb_formulas 库内公式复算）。"""
    symbol_names = list(symbols.keys()) if isinstance(symbols, dict) else list(symbols)
    subs: dict[str, sympy.Expr] = {}
    used: dict[str, float] = {}
    for s in symbol_names:
        if s in inputs:
            subs[s] = sympy.Rational(str(inputs[s]))
            used[s] = inputs[s]
        elif s in defaults:
            subs[s] = sympy.Rational(str(defaults[s]))
            used[s] = float(defaults[s])
        else:
            raise KeyError(f"公式 {formula_name} 缺少输入符号 {s}")
    value = float(sympy.sympify(expr).subs(subs).evalf(12))
    used_text = ", ".join(f"{s}={used[s]}" for s in symbol_names)
    return RecomputeResult(
        formula_key=formula_key,
        value=value,
        unit=unit,
        inputs=used,
        derivation=f"SymPy 复算 {formula_name}: {used_text} → 代入得 {value} {unit}",
    )
