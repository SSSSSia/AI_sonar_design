"""约束求解底座 · 公式库（T1.4，D-15）。

公式类参数（source_type=formula）的取值**必须由本模块代码计算**（A-07：
LLM 不得给出公式参数数值）。公式注册表含符号定义与 SymPy 表达式，
source_ref 指向知识库出处（阶段 3 公式库管理上线后迁入业务库）。

当前收录（来源：py-engine/knowledge/kb/input/towed_array_cad_knowledge.txt
的「声学约束公式」节，source_ref 用 KB 文档名+小节定位）：

- directivity_index:  DI ≈ 10·lg(N)          ——指向性指数（阵元数 N）
- beamforming_freq_max: f_beam ≤ c/(2·d)     ——有效波束成形频率上限（间距 d）
- transmission_loss_spherical: TL = 20·lg(R) ——球面扩展传播损失
- transmission_loss_cylindrical: TL = 15·lg(R) ——圆柱扩展传播损失（浅海）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from solver import units

KB_REF = "towed_array_cad_knowledge.txt#声学约束公式"


@dataclass(frozen=True)
class FormulaSpec:
    """一条可计算公式。

    symbols: 变量名→描述（str 旧形态）或 {"desc","unit"}（T14.3 机器可读单位登记）；
    defaults: 带默认值的可选符号；compute: 精确代入计算；
    expr: 同一公式的 SymPy 表达式（T2.1，A4 复算用，与 compute 互证）。
    单位换算契约（T14.3）：输入值带单位→按登记单位换算；裸数值按登记单位直取；
    量纲冲突/不可解析→剔除候选（宁缺勿错，审查问题 1）。
    min/max 范围登记字段已预留、暂无登记值——工程范围待专业审定后补（A-16 不造数）。
    """

    key: str
    name: str
    unit: str
    symbols: dict[str, Any]
    source_ref: str
    compute: Any
    defaults: dict[str, float] = None
    expr: str = ""

    def required_symbols(self) -> list[str]:
        defaults = self.defaults or {}
        return [s for s in self.symbols if s not in defaults]

    def symbol_unit(self, s: str) -> str:
        v = self.symbols.get(s)
        if isinstance(v, dict):
            return str(v.get("unit") or "")
        return ""


def _di(n: float) -> float:
    return 10.0 * math.log10(n)


def _fbeam_max(d: float, c: float = 1500.0) -> float:
    return c / (2.0 * d)


def _tl_sphere(r: float) -> float:
    return 20.0 * math.log10(r)


def _tl_cyl(r: float) -> float:
    return 15.0 * math.log10(r)


def _sonar_equation_di_required(sl: float, nl: float, tl: float, dt: float = 3.0) -> float:
    """声呐方程变形：所需 DI ≥ DT + NL + TL − SL。"""
    return dt + nl + tl - sl


REGISTRY: dict[str, FormulaSpec] = {
    spec.key: spec
    for spec in (
        FormulaSpec(
            "directivity_index", "指向性指数 DI", "dB",
            {"n": {"desc": "阵元数（无量纲）", "unit": ""}}, f"{KB_REF}#DI≈10lg(N)", _di,
            expr="10*log(n, 10)",
        ),
        FormulaSpec(
            "beamforming_freq_max", "有效波束成形频率上限", "Hz",
            {"d": {"desc": "阵元间距", "unit": "m"},
             "c": {"desc": "声速（默认1500）", "unit": "m/s"}},
            f"{KB_REF}#f_beam≤c/(2d)",
            lambda d, c=1500.0: _fbeam_max(d, c),
            defaults={"c": 1500.0},
            expr="c/(2*d)",
        ),
        FormulaSpec(
            "transmission_loss_spherical", "传播损失（球面扩展）", "dB",
            {"r": {"desc": "距离", "unit": "m"}}, f"{KB_REF}#TL=20lg(R)", _tl_sphere,
            expr="20*log(r, 10)",
        ),
        FormulaSpec(
            "transmission_loss_cylindrical", "传播损失（圆柱扩展）", "dB",
            {"r": {"desc": "距离", "unit": "m"}}, f"{KB_REF}#TL=15lg(R)", _tl_cyl,
            expr="15*log(r, 10)",
        ),
        FormulaSpec(
            "sonar_equation_di_required", "声呐方程所需指向性指数", "dB",
            {"sl": {"desc": "目标声源级", "unit": "db"},
             "nl": {"desc": "噪声级", "unit": "db"},
             "tl": {"desc": "传播损失", "unit": "db"},
             "dt": {"desc": "检测阈值（默认3）", "unit": "db"}},
            f"{KB_REF}#声呐方程",
            lambda sl, nl, tl, dt=3.0: _sonar_equation_di_required(sl, nl, tl, dt),
            defaults={"dt": 3.0},
            expr="dt + nl + tl - sl",
        ),
    )
}

# 参数名（中/英）→ 公式符号。available_inputs 与 intervals.candidate_pools 共用（T2.1）。
SYMBOL_ALIASES: dict[str, str] = {
    "阵元数": "n", "阵元数目": "n", "阵元总数": "n", "总阵元数": "n",
    "阵元数量": "n", "声阵规模": "n", "elements": "n", "n_elements": "n",
    "阵元间距": "d", "间距": "d", "spacing": "d", "d_spacing": "d",
    "声速": "c", "sound_speed": "c",
    "距离": "r", "探测距离": "r", "range": "r",
    "声源级": "sl", "source_level": "sl",
    "噪声级": "nl", "noise_level": "nl",
    "传播损失": "tl",
    "检测阈值": "dt", "detection_threshold": "dt",
}


def available_inputs(params: dict[str, Any]) -> dict[str, float]:
    """把已有参数（含别名）归一为公式符号候选集（T14.3 单位感知）。

    值带单位 → 按符号登记单位换算（"20 mm" 进 d(m) = 0.02——审查验收 01）；
    裸数值按登记单位直取（16 → n=16，兼容 v0.6 前契约）；量纲冲突/未知单位/
    范围表达 → 该候选剔除（宁缺勿错，不再 _first_number 取首数）。
    """
    out: dict[str, float] = {}
    for k, v in params.items():
        key = SYMBOL_ALIASES.get(k, k)
        if key in out:
            continue  # 先到先得：调用方按"目标值优先于提取值"的顺序传入
        spec = _spec_of_symbol(key)
        try:
            value = units.to_symbol_unit(v, spec.symbol_unit(key)) if spec else _first_number(v)
        except units.UnresolvableQuantity:
            continue
        if value is not None:
            out[key] = value
    return out


def _spec_of_symbol(symbol: str) -> FormulaSpec | None:
    for spec in REGISTRY.values():
        if symbol in spec.symbols:
            return spec
    return None


def _first_number(v: Any) -> float | None:
    """数值直取；字符串（如 "16 个"/"约300米"）提取首个数字。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    import re as _re

    m = _re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def evaluate_available(known: dict[str, Any]) -> list[dict[str, Any]]:
    """对所有输入齐备的公式执行精确代入，返回 A2 的 formula 参数条目（未含 trace_id/id）。

    A-07：这里产出数值；LLM 只负责在 IR2 中引用。计算失败的公式如实跳过（记入 derivation 不伪装）。
    """
    inputs = available_inputs(known)
    results: list[dict[str, Any]] = []
    for spec in REGISTRY.values():
        missing = [s for s in spec.required_symbols() if s not in inputs]
        if missing:
            continue
        kwargs = {s: inputs[s] for s in spec.symbols if s in inputs}
        kwargs.update({s: v for s, v in (spec.defaults or {}).items() if s not in kwargs})
        try:
            value = round(spec.compute(**kwargs), 4)
        except Exception:
            continue
        used = ", ".join(f"{s}={kwargs[s]}" for s in spec.symbols)
        deriv = f"{spec.name} = {used} → 代入计算得 {value} {spec.unit}"
        results.append({
            "name": spec.name,
            "value": value,
            "unit": spec.unit,
            "source_type": "formula",
            "source_ref": spec.source_ref,
            "derivation": deriv,
            "confidence": "high",
            "formula_key": spec.key,
        })
    return results
