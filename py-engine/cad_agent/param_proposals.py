# -*- coding: utf-8 -*-
"""E1 CAD→文档反向回写（T11.3，P2-1）：微调定稿几何 diff → 参数变更提案 → 确认回写 IR2。

映射规则（仅长度类联动项，A-16 如实）：
- 基线 = tune.baseline_ir_version 的 IR3（A3 产出，feature.linked_param_id / geometry.linked_dim 有值）；
- 新 IR3 = 微调定稿 geometry_inspect 实测（linked_param_id 一律 None，D8）；
- 按 segment（SEG-xx，part index 对位）对齐基线/新特征：分段集合不一致（编辑增删了分段）→ 不产提案；
- delta = 新实测 bbox[linked_dim] vs 参数登记值（经单位换算到 mm），低于 MIN_DELTA_PCT 的噪声不提案。

回写同步（value + derivation 必须同改）：A4 符号表来自各参数 derivation 的 "symbol=数值"
（solver/recompute.parse_inputs_from_derivation，setdefault 首匹配优先）——只改 value 会让
军标/公式复算沿用旧值。故 derivation 做**原地**首个 ≈旧值 的 symbol=数值 替换 + 尾部追加
人类可读修订记录（不带 = 号，不入符号表）。
"""

from __future__ import annotations

import re
from typing import Any

from agents.a3_cad.geometry_link import UNIT_TO_MM

# 低于该偏差百分比不产提案（几何实测噪声/浮点抖动不算变更）
MIN_DELTA_PCT = 1.0

# symbol=数值（与 solver/recompute._DERIV_INPUT 同构，仅小写符号名）
_SYMBOL_VALUE = re.compile(r"([a-z][a-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)")


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _param_value_mm(param: dict[str, Any]) -> tuple[float, float] | None:
    """(原生值, mm 值)。非长度单位或非正数返回 None（仅长度/直径类可自动映射）。"""
    v = _num(param.get("value"))
    if v is None or v <= 0:
        return None
    factor = UNIT_TO_MM.get(str(param.get("unit") or "").strip().lower())
    if factor is None:
        return None
    return v, v * factor


def diff_proposals(ir2: dict[str, Any], baseline_ir3: dict[str, Any], new_ir3: dict[str, Any]) -> list[dict[str, Any]]:
    """基线 IR3 vs 微调 IR3 → 提案列表（不含 id/状态等存储字段）。

    分段集合不一致时返回 []（无法自动对位，如实不猜）。链式微调基线（前次定稿版，
    linked_param_id 全 None）同理产 0 条。
    """
    params_by_id = {str(p.get("id")): p for p in ir2.get("parameters") or []}
    base_feats = {f.get("segment"): f for f in baseline_ir3.get("features") or []}
    new_feats = {f.get("segment"): f for f in new_ir3.get("features") or []}
    if not base_feats or set(base_feats) != set(new_feats):
        return []

    proposals: list[dict[str, Any]] = []
    seen_params: set[str] = set()
    for seg, bf in sorted(base_feats.items()):
        pid = bf.get("linked_param_id")
        dim = (bf.get("geometry") or {}).get("linked_dim")
        if not pid or dim is None:
            continue
        pid = str(pid)
        param = params_by_id.get(pid)
        if param is None or pid in seen_params:
            continue
        native = _param_value_mm(param)
        if native is None:
            continue
        unit_key = str(param.get("unit") or "").strip().lower()
        new_geo = (new_feats[seg].get("geometry") or {})
        bbox = new_geo.get("bbox_mm") or []
        if not isinstance(dim, int) or dim >= len(bbox):
            continue
        new_mm = _num(bbox[dim])
        if new_mm is None or new_mm <= 0:
            continue
        old_native, old_mm = native
        delta_pct = abs(new_mm - old_mm) / old_mm * 100.0
        if delta_pct < MIN_DELTA_PCT:
            continue
        seen_params.add(pid)
        proposals.append({
            "param_id": pid,
            "param_name": str(param.get("name") or pid),
            "unit": str(param.get("unit") or "mm"),
            "old_value": old_native,
            "new_value": round(new_mm / UNIT_TO_MM[unit_key], 4),
            "delta_pct": round(delta_pct, 2),
            "dimension": int(dim),
        })
    return proposals


def sync_derivation(derivation: str, old_native: float, new_native: float, note: str) -> str:
    """derivation 同步：原地替换首个 ≈旧值 的 symbol=数值（符号表首匹配优先）+ 尾部修订记录。

    数值按字符串替换——符号表解析只认 `[a-z][a-z0-9_]*=数字`，中文注释里的数字不影响；
    找不到可替换模式时仅追加记录（value 字段本身已更新，hit 参数自身求值用 value）。
    """
    old_text = _fmt(old_native)
    new_text = _fmt(new_native)
    out = derivation or ""
    replaced = False

    def _sub(m: re.Match[str]) -> str:
        nonlocal replaced
        if replaced:
            return m.group(0)
        try:
            v = float(m.group(2))
        except ValueError:
            return m.group(0)
        if old_native and abs(v - old_native) <= max(1e-9, abs(old_native) * 1e-6):
            replaced = True
            return f"{m.group(1)}={new_text}"
        return m.group(0)

    out = _SYMBOL_VALUE.sub(_sub, out)
    # 修订记录不带 "符号=" 形态，避免进入符号表（A-16 留痕要求）
    out = f"{out}\n\n> E1 实测修订：{note}（原 {old_text} → {new_text}，来源 geometry_inspect 实测）".strip()
    return out


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(round(v, 4))


def apply_proposals(ir2: dict[str, Any], applied: list[dict[str, Any]], round_no: int) -> dict[str, Any]:
    """确认回写：按提案改 value + derivation，design_document 追加修订段。返回新 IR2 content。

    applied 元素 = 提案行 + ["ir3_version"]；深拷贝不改旧快照（A-12 不可变）。
    """
    import copy

    new_ir2 = copy.deepcopy(ir2)
    params = new_ir2.get("parameters") or []
    by_id = {str(p.get("id")): p for p in params}
    lines: list[str] = []
    for a in applied:
        p = by_id.get(str(a["param_id"]))
        if p is None:
            continue
        note = f"微调 r{round_no} {a['param_name']}（{a['param_id']}）"
        p["value"] = a["new_value"]
        p["derivation"] = sync_derivation(str(p.get("derivation") or ""),
                                          float(a["old_value"]), float(a["new_value"]), note)
        lines.append(f"| {a['param_id']} | {a['param_name']} | {a['old_value']} | {a['new_value']} "
                     f"| {a['unit']} | {a['delta_pct']}% |")
    doc = str(new_ir2.get("design_document") or "")
    doc += ("\n\n## 修订记录（E1 CAD→文档反向回写）\n\n"
            f"微调定稿 r{round_no} 实测几何与登记参数存在偏差，经人工确认回写：\n\n"
            "| 参数ID | 名称 | 原值 | 实测值 | 单位 | 偏差 |\n|---|---|---|---|---|---|\n"
            + "\n".join(lines))
    new_ir2["design_document"] = doc
    return new_ir2
