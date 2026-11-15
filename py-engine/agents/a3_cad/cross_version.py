# -*- coding: utf-8 -*-
"""跨版本分段对应（T15.4，ADR-014 §6）——两版实测分段的旋转不变身份对应能力。

定位（DEV-001 §2.2 B-3「对 E1 对位的收益另验证」）：本模块只提供**能力与反例评测**，
不接入 E1——``cad_agent/param_proposals.diff_proposals`` 的 SEG 标签位置对位保持不动；
将来接入点与收益验证（提案准确率/召回 对比位置对位）独立排期（另验证）。

为什么要新证据（旧身份的破坏点）：
- ``geometry_inspect._finalize`` 按整体最长轴升序编号——整体旋转换轴后 SEG 重编号，
  位置身份失效（旋转反例）；
- 拆分/合并改变分段数，位置对位无解（E1 现口径如实产 0 提案）。

对应证据（全部旋转不变，不依赖编号顺序）：
- ``volume_mm3``（本征量）；
- ``radius_mm``（圆度证据，任一侧缺省则该维不参与）；
- ``sorted(bbox_mm)`` 多重集（各维随旋转换轴，多重集不变）。

对应语义（与声明制/legacy 候选同一套四态，ADR-014 §2/§6）：
- verified：唯一最优对应（首2候选距离差 > 并列阈），距离如实记录；
- ambiguous：首2候选并列（同尺寸/对称反例——个体对应不可分辨，如实）；
- unmatched：候选全被更优基段占用或新版无候选；新版多余段（拆分产物）以
  base_index=None 的 unmatched 条目如实呈现——不假装一一对应；
- 距离**不设门槛**（对齐声明制「绑定不设数值门槛」口径：同一对象经大改仍是同一
  身份，偏差由读侧裁决）；合并 = 多基段争用同一段 → 距离最优者 verified、其余
  unmatched（合并形态以 surplus 基段可见）。

纯函数模块（无 IO / 无 LLM）。不进 IR 快照（无 schema 变更），仅供评测与将来的
E1 收益验证消费。
"""

from __future__ import annotations

from typing import Any

# 并列阈（偏差百分点）——与 identity._TIE_EPS_PCT 同口径（并列=无法区分个体→ambiguous）
_TIE_EPS_PCT = 0.01

_VERSION = "1"


def _signature_distance(base: dict[str, Any], new: dict[str, Any]) -> float | None:
    """基段↔新段签名距离（各可用证据维的最差相对偏差，pp）；无任何可比证据 → None。"""
    devs: list[float] = []
    vb, vn = base.get("volume_mm3"), new.get("volume_mm3")
    if isinstance(vb, (int, float)) and vb > 0 and isinstance(vn, (int, float)) and vn > 0:
        devs.append(abs(float(vn) - float(vb)) / float(vb) * 100.0)
    bb, bn = base.get("bbox_mm") or [], new.get("bbox_mm") or []
    if len(bb) == 3 and len(bn) == 3:
        for a, b in zip(sorted(float(x) for x in bb), sorted(float(x) for x in bn)):
            if a > 0:
                devs.append(abs(b - a) / a * 100.0)
    rb, rn = base.get("radius_mm"), new.get("radius_mm")
    if isinstance(rb, (int, float)) and rb > 0 and isinstance(rn, (int, float)) and rn > 0:
        devs.append(abs(float(rn) - float(rb)) / float(rb) * 100.0)
    return max(devs) if devs else None


def match_parts_between_versions(
    base_parts: list[dict[str, Any]],
    new_parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """基线 IR3 实测分段 ↔ 新版实测分段 → 旋转不变对应（四态，T15.4）。

    返回 ``{"version": "1", "matches": [...]}``；条目：
    ``{"base_index": int|None, "new_index": int|None, "status": verified|ambiguous|unmatched,
      "distance_pct": float|None}``——以基段为锚逐段给出（index 为 geometry_inspect
    的 1-based part index），新版多余段（拆分产物）追加 base_index=None 的条目。
    空 `new_parts` → 全部基段 unmatched（如实）；两侧均空 → matches=[]。
    """
    cands_by_base: dict[int, list[tuple[float, int]]] = {}
    for bp in base_parts:
        cands = sorted(
            ((d, np_["index"]) for np_ in new_parts
             if (d := _signature_distance(bp, np_)) is not None),
            key=lambda t: (t[0], t[1]),
        )
        cands_by_base[int(bp["index"])] = cands

    matches: list[dict[str, Any]] = []
    taken: set[int] = set()
    clear: list[tuple[float, int, int]] = []       # (distance, base_index, new_index)
    for bidx, cands in cands_by_base.items():
        if not cands:
            matches.append({"base_index": bidx, "new_index": None,
                            "status": "unmatched", "distance_pct": None})
            continue
        if len(cands) >= 2 and cands[1][0] - cands[0][0] <= _TIE_EPS_PCT:
            matches.append({"base_index": bidx, "new_index": None,
                            "status": "ambiguous", "distance_pct": None})
            continue
        clear.append((cands[0][0], bidx, cands[0][1]))
    clear.sort(key=lambda t: (t[0], t[1]))
    for dist, bidx, nidx in clear:
        if nidx in taken:
            matches.append({"base_index": bidx, "new_index": None,
                            "status": "unmatched", "distance_pct": None})
            continue
        taken.add(nidx)
        matches.append({"base_index": bidx, "new_index": nidx,
                        "status": "verified", "distance_pct": round(dist, 4)})

    # 新版多余段（无基段对应，拆分产物）：base_index=None 如实呈现
    matched_new = {m["new_index"] for m in matches if m["new_index"] is not None}
    for np_ in sorted(new_parts, key=lambda p: p["index"]):
        if int(np_["index"]) not in matched_new:
            matches.append({"base_index": None, "new_index": int(np_["index"]),
                            "status": "unmatched", "distance_pct": None})
    return {"version": _VERSION, "matches": matches}
