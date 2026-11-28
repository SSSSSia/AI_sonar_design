# -*- coding: utf-8 -*-
"""STEP 质量实测（T19.1：连通性必检从 harness 升格为生产模块，契约=ADR-018 决策 1）。

check_connectivity 原样迁移自 scripts/gen_ref_candidates.py `_connectivity`（harness 改经
import 复用，行为逐字节）——教训=拖鱼 v2 翼板与主体仅线相切、拖曳环悬空：重放 PASS+bbox/
体积证据不测「部件之间连没连」。measure_step 供配方抽样验证（T19.1）与上传工装（T19.5）复用。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# 连通性两两核对上限（ADR-018 决策 1 新增必检项）：超过只报数量不判连通（组合爆炸，如实 N/A）
_MAX_PAIRWISE_SOLIDS = 12


def check_connectivity(step_file) -> dict[str, Any]:
    """连通性实测（2026-11-26 裁决新增入库必检项，ADR-018）：实体数/有效性/两两重叠建图
    判连通分量。教训=拖鱼 v2 翼板与主体仅线相切、拖曳环悬空——重放 PASS+bbox/体积证据
    不测「部件之间连没连」（docs/reviews/t164-candidates/REPORT.md 裁决记录节）。
    connected 判据=isValid 且全部实体在同一个重叠连通分量（union-find）；
    min_pair_overlap_ratio=相连实体对的最小重叠体积比（对较小实体体积归一）。"""
    out: dict = {"measured": False}
    try:
        import tempfile

        import cadquery as cq

        data = Path(step_file).read_bytes()
        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            shape = cq.importers.importStep(tmp).val()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        solids = shape.Solids()  # cadquery 2.x 直接返回 list（T17.5 实测）
        out["solids"] = len(solids)
        out["valid"] = bool(shape.isValid())
        out["measured"] = True
        if len(solids) > _MAX_PAIRWISE_SOLIDS:
            out["connected"] = None  # 超上限不判（A-16 如实 N/A）
            out["reason"] = f"solids={len(solids)} 超两两核对上限 {_MAX_PAIRWISE_SOLIDS}"
            return out
        parent = list(range(len(solids)))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        vols = [s.Volume() for s in solids]
        ratios: list[float] = []
        for i in range(len(solids)):
            for j in range(i + 1, len(solids)):
                try:
                    inter = solids[i].intersect(solids[j])
                    ov = sum(s.Volume() for s in inter.Solids()) if inter.Solids() else 0.0
                except Exception:  # noqa: BLE001 - 不相交/布尔失败按零重叠（如实）
                    ov = 0.0
                if ov > 1e-6:
                    parent[_find(i)] = _find(j)
                    ratios.append(ov / (min(vols[i], vols[j]) or 1.0))
        components = len({_find(i) for i in range(len(solids))})
        out["components"] = components
        out["min_pair_overlap_ratio"] = round(min(ratios), 6) if ratios else None
        out["connected"] = components == 1 and out["valid"]
        return out
    except Exception as exc:  # noqa: BLE001 - 实测失败如实不抛（harness 纪律）
        out["reason"] = str(exc)[:200]
        return out


def measure_step(step_file) -> dict[str, Any]:
    """首个 STEP 的几何实测摘要（A-16：实测什么写什么，不造值）。

    overall bbox_mm=[dx,dy,dz]（extents，geometry_inspect 契约）。"""
    from cad_engine.geometry_inspect import inspect_step

    try:
        insp = inspect_step(Path(step_file).read_bytes())
    except OSError as exc:
        return {"measured": False, "reason": str(exc)[:200]}
    if not insp.get("ok"):
        return {"measured": False, "reason": str(insp.get("error"))[:200]}
    overall = insp.get("overall") or {}
    return {
        "measured": True,
        "overall_bbox_mm": overall.get("bbox_mm"),
        "overall_volume_mm3": overall.get("volume_mm3"),
        "part_count": overall.get("part_count"),
    }
