"""单位统一模块（T14.3，外部审查问题 1）——量纲解析与换算的单一事实源。

背景（审查【已复现】）：_first_number 纯抽数字丢弃单位，"20 mm" 与 "0.02 m"
进入按 SI 写死的公式闭包结果差 1000 倍。本模块把参数值解析为 (value, unit, kind)
并按量纲族换算到目标单位，A2 公式计算 / A4 几何联动 / E1 回写共用。

原则（审查验收 01/14）：
- 同物理量等价写法（20 mm / 2 cm / 0.02 m）必须得到同一归一值与公式结果；
- 裸数值按"公式符号登记单位"直取（与 v0.6 前契约兼容——16 就是 16 个阵元）；
- 未知单位、范围表达（"20-30 米"）不猜不取首数——抛 UnresolvableQuantity，
  上游剔除该候选并如实"未验证"，绝不静默代入。

覆盖族（本期边界，A-16 如实）：length（规范 mm）、frequency（规范 Hz）、
level（dB，恒等）、speed（m/s，恒等）、dimensionless（数字/中文计数词）。
角度/温度等族按需后补——遇到未登记单位一律 Unresolvable，不扩展猜测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class UnresolvableQuantity(ValueError):
    """不可解析/不可换算的量（未知单位/范围表达/量纲冲突）——上游必须显式处置。"""


@dataclass(frozen=True)
class Quantity:
    """解析结果。unit 为归一 token（小写英文；"" = 无量纲）。"""

    value: float
    unit: str
    kind: str  # length | frequency | level | speed | none


# 量纲族注册：token → (kind, 换算到族规范单位的因子)。规范单位取各族 SI 口径。
_FAMILIES: dict[str, tuple[str, float]] = {
    "mm": ("length", 1.0), "毫米": ("length", 1.0),
    "cm": ("length", 10.0), "厘米": ("length", 10.0),
    "dm": ("length", 100.0), "分米": ("length", 100.0),
    "m": ("length", 1000.0), "米": ("length", 1000.0),
    "hz": ("frequency", 1.0), "khz": ("frequency", 1e3), "mhz": ("frequency", 1e6),
    "db": ("level", 1.0), "分贝": ("level", 1.0),
    "m/s": ("speed", 1.0), "米/秒": ("speed", 1.0),
    "kn": ("speed", 1852.0 / 3600.0), "节": ("speed", 1852.0 / 3600.0),  # 1 kn = 1852 m/h（精确）
}
# 中文/异写 → 归一英文 token
_CANONICAL = {"毫米": "mm", "厘米": "cm", "分米": "dm", "米": "m",
              "分贝": "db", "米/秒": "m/s", "节": "kn"}
# 无量纲中文计数词（"16 个" → 16，不换算）
_COUNT_WORDS = {"个", "只", "枚", "阵元", "通道"}

# 长度族 → mm 因子（原 geometry_link.UNIT_TO_MM 收口至此，含中文别名超集）
LENGTH_TO_MM: dict[str, float] = {
    tok: factor for tok, (kind, factor) in _FAMILIES.items() if kind == "length"
}

# 数字 + 可选单位 token（长 token 优先：mm 先于 m、khz 先于 hz、m/s 先于 m）
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_UNIT_TOKEN = re.compile(
    r"(mm|cm|dm|khz|mhz|m/s|hz|db|kn|分贝|米/秒|毫米|厘米|分米|米|个|只|枚|节|阵元|通道|m)",
    re.IGNORECASE,
)


def canonical_unit(unit: str) -> str:
    """单位 token 归一（中文别名 → 英文规范；大小写不敏感）。"""
    u = (unit or "").strip().lower()
    return _CANONICAL.get(u, u)


def unit_kind(unit: str) -> str | None:
    """单位所属量纲族；未知/空返回 None（空串视为无量纲 none 由调用方约定）。"""
    fam = _FAMILIES.get(canonical_unit(unit))
    return fam[0] if fam else None


def parse_quantity(v) -> Quantity:
    """参数值 → Quantity。数值直取；字符串取首个数值+紧邻单位。

    多数值（范围/组合表达）与数字后跟随的未识别内容一律抛 UnresolvableQuantity
    （不取首数继续算——审查问题 1 的核心口径）；数值**前**的修饰文字（"约/大于"）
    忽略，数值本身仍然明确。未知单位需先在本模块登记族后方可使用。
    """
    if isinstance(v, bool):
        raise UnresolvableQuantity(f"布尔值不是物理量: {v!r}")
    if isinstance(v, (int, float)):
        return Quantity(float(v), "", "none")
    s = str(v).strip()
    if not s:
        raise UnresolvableQuantity("空字符串不是物理量")
    m = _NUM.search(s)
    if not m:
        raise UnresolvableQuantity(f"无法从 {v!r} 解析数值")
    value = float(m.group())
    rest = s[m.end():].strip()
    if _NUM.search(rest):
        raise UnresolvableQuantity(f"{v!r} 含多个数值（范围/组合表达），请给出明确取值")
    if not rest:
        return Quantity(value, "", "none")
    tu = _UNIT_TOKEN.match(rest)
    token = tu.group(1).lower() if tu else ""
    if not token:
        raise UnresolvableQuantity(
            f"{v!r} 数字后跟随不可识别内容 {rest[:8]!r}——请给出明确数值与登记单位")
    if token in _COUNT_WORDS:
        return Quantity(value, "", "none")
    fam = _FAMILIES.get(token)
    if fam is None:
        raise UnresolvableQuantity(f"未知单位 {token!r}（{v!r}）——未登记量纲，请改用明确单位")
    return Quantity(value, canonical_unit(token), fam[0])


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """同族换算；跨族/未知单位抛 UnresolvableQuantity（"" == "" 恒等）。"""
    fu, tu = canonical_unit(from_unit), canonical_unit(to_unit)
    if fu == tu:
        return value
    ff, tf = _FAMILIES.get(fu), _FAMILIES.get(tu)
    if ff is None:
        raise UnresolvableQuantity(f"未知来源单位 {from_unit!r}")
    if tf is None:
        raise UnresolvableQuantity(f"未知目标单位 {to_unit!r}")
    if ff[0] != tf[0]:
        raise UnresolvableQuantity(f"量纲冲突：{from_unit!r} 与 {to_unit!r} 不同族")
    return value * ff[1] / tf[1]


def to_symbol_unit(value_text, symbol_unit: str) -> float:
    """把参数值（标量或带单位文本）换算到公式符号登记单位。

    裸数值按符号单位直取（兼容 v0.6 前契约）；带单位则同族换算；
    不可解析/量纲冲突抛 UnresolvableQuantity。"""
    q = parse_quantity(value_text)
    su = canonical_unit(symbol_unit)
    if q.kind == "none" and not q.unit:
        return q.value
    if not su:
        raise UnresolvableQuantity(f"符号未登记单位，输入却带单位 {q.unit!r}")
    return convert(q.value, q.unit, su)
