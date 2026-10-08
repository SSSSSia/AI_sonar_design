# -*- coding: utf-8 -*-
"""E1 CAD→文档反向回写（T11.3，P2-1）：微调定稿几何 diff → 参数变更提案 → 确认回写 IR2。

映射规则（仅长度类联动项，A-16 如实）：
- 基线 = tune.baseline_ir_version 的 IR3（A3 产出，feature.linked_param_id / geometry.linked_dim 有值）；
- 新 IR3 = 微调定稿 geometry_inspect 实测（linked_param_id 一律 None，D8）；
- 按 segment（SEG-xx，part index 对位）对齐基线/新特征：分段集合不一致（编辑增删了分段）→ 不产提案；
- delta = 新实测 bbox[linked_dim] vs 参数登记值（经单位换算到 mm），低于 MIN_DELTA_PCT 的噪声不提案。

回写同步（value + derivation 必须同改）：A4 符号表来自各参数 derivation 的 "symbol=数值"
（solver/recompute.parse_inputs_from_derivation，setdefault 首匹配优先）——只改 value 会让
军标/公式复算沿用旧值。故 derivation 做**目标符号**的 ≈旧值 原地替换（T14.2：优先参数
亲缘符号精确定位，同值歧义不内联只留痕——不再"首个 ≈旧值"盲替换）+ 尾部追加人类可读
修订记录（不带 = 号，不入符号表）。
"""

from __future__ import annotations

import re
from typing import Any

from agents.a3_cad.geometry_link import UNIT_TO_MM

# 低于该偏差百分比不产提案（几何实测噪声/浮点抖动不算变更）
MIN_DELTA_PCT = 1.0

# symbol=数值（与 solver/recompute._DERIV_INPUT 同构，仅小写符号名）
_SYMBOL_VALUE = re.compile(r"([a-z][a-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)")

# symbol 与 param id 的亲缘比较：只留字母数字（P-001→p001）
_IDENT_CLEAN = re.compile(r"[^a-z0-9]")


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _approx_old(value: float, old_native: float) -> bool:
    return bool(old_native) and abs(value - old_native) <= max(1e-9, abs(old_native) * 1e-6)


def merge_by_param(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """同参数多轮提案归并（T14.2，审查问题 5a）：每参数只保留 (round_no, created_at)
    最新的提案，其余为被取代者——"全部应用"时旧提案不再反向覆盖新值。

    返回 (winners, losers)：winners 按 (round_no, created_at) 升序，应用顺序无关紧要
    （参数互不重复）；losers 交调用方标 superseded。"""
    ordered = sorted(rows, key=lambda r: (int(r.get("round_no") or 0),
                                          str(r.get("created_at") or ""), str(r.get("id") or "")))
    winners: dict[str, dict[str, Any]] = {}
    losers: list[dict[str, Any]] = []
    for r in ordered:
        pid = str(r.get("param_id"))
        if pid in winners:
            losers.append(winners[pid])
        winners[pid] = r
    return list(winners.values()), losers


def _own_symbol(param: dict[str, Any], old_native: float) -> str | None:
    """定位参数自身符号：推导文本中 ≈旧值 的 symbol 候选里选与 param id 有亲缘的
    （归一化后相等/后缀/包含，如 ARR_D↔d、seg_length↔length）；无亲缘返回 None，
    交 sync_derivation 的单值/歧义规则处置（宁缺勿错）。"""
    pid = _IDENT_CLEAN.sub("", str(param.get("id") or "").lower())
    cands: list[str] = []
    for m in _SYMBOL_VALUE.finditer(str(param.get("derivation") or "")):
        try:
            v = float(m.group(2))
        except ValueError:
            continue
        if _approx_old(v, old_native):
            cands.append(m.group(1))
    for sym in cands:
        s = _IDENT_CLEAN.sub("", sym.lower())
        if pid and s and (s == pid or pid.endswith(s) or s in pid):
            return sym
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


def sync_derivation(derivation: str, old_native: float, new_native: float, note: str,
                    symbol: str | None = None) -> str:
    """derivation 同步：原地替换目标符号的 symbol=数值 + 尾部修订记录（T14.2）。

    替换定位（审查问题 5b：不再"首个 ≈旧值"盲替换——width/length 同值会串改）：
    1. symbol 给定 → 只替换 symbol 精确匹配（大小写不敏感）的片段；
    2. symbol 缺失/未命中 → 全文恰有一个 ≈旧值 片段时替换它；
    3. 同值歧义（多个符号同值）且无法定位 → **不内联替换**（宁缺勿错），仅追加修订
       记录（value 字段本身已更新；A4 取数源结构化切换后推导文本只承担展示——T14.7）。
    数值按字符串替换——符号表解析只认 `[a-z][a-z0-9_]*=数字`，中文注释里的数字不影响。
    """
    old_text = _fmt(old_native)
    new_text = _fmt(new_native)
    out = derivation or ""

    matches = []
    for m in _SYMBOL_VALUE.finditer(out):
        try:
            v = float(m.group(2))
        except ValueError:
            continue
        if _approx_old(v, old_native):
            matches.append(m)

    target = None
    if symbol:
        sym = symbol.strip().lower()
        target = next((m for m in matches if m.group(1).lower() == sym), None)
    if target is None and len(matches) == 1:
        target = matches[0]
    if target is not None:
        out = out[:target.start()] + f"{target.group(1)}={new_text}" + out[target.end():]
    elif len(matches) > 1:
        note = f"{note}（推导含 {len(matches)} 个同值符号，定位失败未内联替换，仅留痕）"

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
                                          float(a["old_value"]), float(a["new_value"]), note,
                                          symbol=_own_symbol(p, float(a["old_value"])))
        lines.append(f"| {a['param_id']} | {a['param_name']} | {a['old_value']} | {a['new_value']} "
                     f"| {a['unit']} | {a['delta_pct']}% |")
    doc = str(new_ir2.get("design_document") or "")
    doc += ("\n\n## 修订记录（E1 CAD→文档反向回写）\n\n"
            f"微调定稿 r{round_no} 实测几何与登记参数存在偏差，经人工确认回写：\n\n"
            "| 参数ID | 名称 | 原值 | 实测值 | 单位 | 偏差 |\n|---|---|---|---|---|---|\n"
            + "\n".join(lines))
    new_ir2["design_document"] = doc
    return new_ir2


def apply_forward_update(ir2: dict[str, Any], param_id: str, new_value: float,
                         reason: str | None) -> dict[str, Any]:
    """E1 正向更新（T12.2）：改 IR2 单参数 value + derivation 原地同步 + design_document 修订段。

    与反向回写共用符号表同步纪律（sync_derivation）；深拷贝不改旧快照（A-12）。"""
    import copy

    new_ir2 = copy.deepcopy(ir2)
    param = next((p for p in new_ir2.get("parameters") or [] if str(p.get("id")) == str(param_id)), None)
    if param is None:
        raise KeyError(f"参数不存在: {param_id}")
    old_value = _num(param.get("value"))
    note = f"E1 正向更新（{reason or '人工修订'}）"
    param["value"] = new_value
    param["derivation"] = sync_derivation(str(param.get("derivation") or ""),
                                          float(old_value), float(new_value), note,
                                          symbol=_own_symbol(param, float(old_value)))
    doc = str(new_ir2.get("design_document") or "")
    unit = str(param.get("unit") or "")
    doc += (f"\n\n## 修订记录（E1 正向参数更新）\n\n"
            f"- 参数：{param.get('name')}（{param_id}）\n"
            f"- 变更：{_fmt(float(old_value))} → {_fmt(float(new_value))} {unit}\n"
            f"- 理由：{reason or '—'}\n"
            f"- 后续：CAD 将按新参数重新建模（A3 重算），经审阅关②复审")
    new_ir2["design_document"] = doc
    return new_ir2
