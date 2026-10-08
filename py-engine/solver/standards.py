"""军标条目核对框架（T2.1 占位 → T3.3 业务库接线 → T9.3 表达式求值激活）。

A-16：军标库为空时必须如实输出"待补"，禁止伪装成已核对。
核对条目两个来源：内存 REGISTRY_STANDARDS（带 check 函数的自动化规则）+
业务库 kb_standards（V7 建表 + V12 增 expr 判定表达式列）。

T9.3 求值口径（PHASE-009 §2.3）：
- 库内条目带 expr（SymPy 布尔表达式，如 outer_diameter <= 325）→ 自动求值：
  成立 → pass（计入 CSR）；不成立 → violation（error + back_to_A2）；
  引用符号在 IR2 无对应值 → uncovered（info 如实提示未覆盖，不算违反）；
  解析/求值失败 → 按"登记待补"如实降级，不硬失败。
- 符号表 = IR2 各参数 derivation 的 "符号=数值" 记录（与公式复算同源，
  solver/recompute.parse_inputs_from_derivation）+ value（命中参数的登记值）。
  T14.3 兑现「单位换算接入」：长度族参数的 value 换算到 mm 再绑定（demo 条目
  长度口径为 mm，"0.02 m" 不再被当成 0.02mm 参与判定）；其余族/裸值按原值——
  推导文本符号仍为原始数值（T14.7 切换取数源后一并结构化）。
- 求值安全：parse_expr 限定受限 global_dict（禁 builtins/禁 import），只放行数学函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import sympy

from solver import units
from solver.recompute import parse_inputs_from_derivation


@dataclass(frozen=True)
class StandardSpec:
    """一条军标核对规则。

    code: 军标编号（如 "GJB 585"）；applies_to: 参数名包含匹配（空串不匹配任何参数）；
    expr: SymPy 布尔判定表达式（T9.3，库内条目）；check: (param) -> str | None 兼容旧接口
    （返回违例说明；None 表示仅登记）——库内条目不再使用，保留给引擎侧内存规则。
    """

    code: str
    name: str
    applies_to: str
    expr: str | None = None
    check: Callable[[dict[str, Any]], str | None] | None = None


# 引擎侧内存规则（自动化核对）；业务库条目经 load_enabled_standards 叠加
REGISTRY_STANDARDS: list[StandardSpec] = []

# kb_standards 库内条目缓存（T3.3；库不可达/为空→[]，维持"待补"诚实语义）
_db_cache: tuple[float, list[StandardSpec]] | None = None
_DB_TTL_S = 30


def load_enabled_standards(max_age_s: int = _DB_TTL_S) -> list[StandardSpec]:
    """读业务库 kb_standards（enabled）→ StandardSpec（含 expr，T9.3）。

    库不可达/为空返回 []；失败也写缓存（TTL 内不重复打库）。
    """
    global _db_cache
    import time as _time

    now = _time.time()
    if _db_cache is not None and now - _db_cache[0] < max_age_s:
        return _db_cache[1]
    specs: list[StandardSpec] = []
    try:
        from storage.registry_store import list_rows_cached

        for row in list_rows_cached("standards"):
            specs.append(StandardSpec(
                code=str(row["code"]),
                name=str(row["name"]),
                applies_to=str(row.get("applies_to") or ""),
                expr=(str(row["expr"]).strip() or None) if row.get("expr") else None,
                check=None,
            ))
    except Exception:
        specs = []
    _db_cache = (now, specs)
    return specs


def reset_cache() -> None:
    """业务库变更后清缓存（管理 API 调用）。"""
    global _db_cache
    _db_cache = None


def _all_standards() -> list[StandardSpec]:
    return REGISTRY_STANDARDS + load_enabled_standards()


# ----------------------------------------------------------------------
# 表达式求值（T9.3）
# ----------------------------------------------------------------------
# 受限全局命名空间：禁 builtins/禁 import，只放行数学函数、常数与 sympy 数值构造器
# （parse_expr 的字面量转换依赖 Integer/Float/Rational/Symbol，缺了会解析失败）
_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},
    "sqrt": sympy.sqrt, "exp": sympy.exp, "log": sympy.log, "Abs": sympy.Abs,
    "pi": sympy.pi, "E": sympy.E, "oo": sympy.oo, "min": min, "max": max,
    "log10": lambda x: sympy.log(x, 10), "lg": lambda x: sympy.log(x, 10),
    "Integer": sympy.Integer, "Float": sympy.Float, "Rational": sympy.Rational,
    "Symbol": sympy.Symbol, "And": sympy.And, "Or": sympy.Or, "Not": sympy.Not,
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _symbol_table(param: dict[str, Any], all_params: list[dict[str, Any]]) -> dict[str, float]:
    """符号表：IR2 各参数 derivation 的 symbol=value 记录（与公式复算同源）
    + value 绑定命中参数登记值。同名符号冲突时，命中参数自身的 derivation 优先
    （跨参数符号以其参数各自的登记为准，避免"先到先得"的任意性）。"""
    table: dict[str, float] = {}
    for p in all_params:
        for sym, val in parse_inputs_from_derivation(p.get("derivation") or "").items():
            table.setdefault(sym, val)
    for sym, val in parse_inputs_from_derivation(param.get("derivation") or "").items():
        table[sym] = val  # 命中参数自身登记覆盖
    own = _num(param.get("value"))
    if own is not None:
        unit = str(param.get("unit") or "").strip().lower()
        try:
            # T14.3：长度族登记值统一换算到 mm 再绑定（demo 条目长度口径 mm）
            table["value"] = units.convert(own, unit, "mm") if units.unit_kind(unit) == "length" else own
        except units.UnresolvableQuantity:
            table["value"] = own  # 未知单位按原值绑定（覆盖面边界如实，A-16）
    return table


def _evaluate(expr: str, table: dict[str, float]) -> tuple[str, str]:
    """对表达式安全求值。返回 (verdict, detail)，verdict ∈ pass|violation|uncovered|invalid。"""
    local = {k: sympy.Rational(str(v)) for k, v in table.items()}
    try:
        parsed = sympy.parse_expr(expr, local_dict=local, global_dict=_SAFE_GLOBALS)
    except Exception as exc:  # noqa: BLE001 - 解析失败如实降级为 invalid
        return "invalid", f"表达式解析失败（{exc}）"
    missing = {str(s) for s in parsed.free_symbols} - set(table)
    if missing:
        return "uncovered", f"引用符号 {sorted(missing)} 在 IR2 参数 derivation 中无对应值"
    if not isinstance(parsed, sympy.logic.boolalg.Boolean):  # Relational 亦为 Boolean 子类
        return "invalid", "表达式须为可判定的布尔表达式（如 outer_diameter <= 325）"
    try:
        ok = bool(parsed)
    except Exception as exc:  # noqa: BLE001
        return "invalid", f"表达式求值失败（{exc}）"
    if ok:
        return "pass", ""
    # parse_expr 在解析期即把 local_dict 数值代入（free_symbols 为空），审计文案改从
    # 表达式字符串与符号表的交集提取实际代入的符号
    import re as _re

    names = sorted(set(_re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)) & set(table))
    used = ", ".join(f"{s}={table[s]:g}" for s in names)
    return "violation", f"代入 {used} 后不成立"


def check_param(param: dict[str, Any],
                all_params: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对单个 source_type=standard 的参数给出核对结论。

    返回 {status, message, standards?}：
    - status="pass"：命中条目且判定全部通过（A4 计入 CSR）
    - status="violation"：任一条目判定不成立（A4 出 error + back_to_A2）
    - status="uncovered"：条目引用符号在 IR2 无对应值——如实提示未覆盖，不算违反（A-16）
    - status="pending"：无可自动核对的条目（库空/仅登记无表达式/表达式无效）——如实待补
    """
    name = param.get("name") or ""
    params = all_params if all_params is not None else [param]
    matched = [s for s in _all_standards() if s.applies_to and s.applies_to in name]
    if not matched:
        return {
            "status": "pending",
            "message": (
                f"军标核对待补：军标库暂无覆盖「{name}」的条目"
                f"（当前库共 {len(_all_standards())} 条），无法自动核对"
            ),
        }
    table = _symbol_table(param, params)
    violations: list[str] = []
    passed: list[str] = []
    uncovered: list[str] = []
    registered: list[str] = []
    entries: list[dict[str, Any]] = []  # T14.4：逐条目 verdict（coverage 计数用）
    for spec in matched:
        if spec.check is not None:  # 引擎侧内存规则（旧接口，保留兼容）
            issue = spec.check(param)
            if issue:
                violations.append(f"{spec.code}《{spec.name}》：{issue}")
                entries.append({"code": spec.code, "verdict": "violation", "has_expr": False})
            else:
                passed.append(spec.code)
                entries.append({"code": spec.code, "verdict": "pass", "has_expr": False})
            continue
        if not spec.expr:
            registered.append(f"{spec.code}《{spec.name}》已登记（无判定表达式，提示性核对）")
            entries.append({"code": spec.code, "verdict": "registered", "has_expr": False})
            continue
        verdict, detail = _evaluate(spec.expr, table)
        entries.append({"code": spec.code, "verdict": verdict, "has_expr": True})
        if verdict == "pass":
            passed.append(spec.code)
        elif verdict == "violation":
            violations.append(f"{spec.code}《{spec.name}》：{spec.expr} —— {detail}")
        elif verdict == "uncovered":
            uncovered.append(f"{spec.code}《{spec.name}》{detail}")
        else:
            registered.append(f"{spec.code}《{spec.name}》{detail}")

    result: dict[str, Any] = {"standards": [s.code for s in matched], "entries": entries}
    if violations:
        result.update({"status": "violation", "message": "；".join(violations)})
        return result
    if passed:
        msg = f"已按 {', '.join(passed)} 判定通过"
        if uncovered:
            msg += "；" + "；".join(uncovered)
        result.update({"status": "pass", "message": msg})
        return result
    if uncovered:
        result.update({"status": "uncovered",
                       "message": f"参数「{name}」：" + "；".join(uncovered)})
        return result
    if registered:
        result.update({"status": "pending", "message": f"参数「{name}」：" + "；".join(registered)})
        return result
    result.update({"status": "pending", "message": f"参数「{name}」：无有效核对条目"})
    return result
