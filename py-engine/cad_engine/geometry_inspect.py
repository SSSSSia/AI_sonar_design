# -*- coding: utf-8 -*-
"""几何提取模块（T7.2，PHASE-007）：STEP → 整体/分段实测几何 JSON。

双入口：
- 进程内函数 ``inspect_step(step_bytes) -> dict``（单测/工具用）；
- CLI ``python geometry_inspect.py <step_path> <out_json>``——A3 以 **subprocess** 调用本入口，
  复刻 convert_worker.py 的 GIL 隔离范式：OCCT/cadquery/trimesh 镶嵌是 CPU 密集的 C 扩展
  调用，禁止在 agent/API 进程内执行（2026-09-20 实机教训）。

输出 JSON 契约（A3 _build_ir3 / A4 数值复核依赖此结构，字段变更需同步消费方并同 commit 改测试）::

    {
      "ok": true,
      "overall": {"bbox_mm": [dx, dy, dz], "volume_mm3": float, "part_count": int},
      "parts": [
        {"index": 1, "center_mm": [x, y, z], "bbox_mm": [dx, dy, dz], "volume_mm3": float}
      ],
      "meta": {"extractor": "cadquery-occt/trimesh", "tolerance_mm": float, "elapsed_s": float,
               "segmentation": "connected_components|radius_profile|fallback_single"}
    }

- 单位一律 mm / mm³（STEP 默认单位制）；center_mm 为该段包围盒中心；
  parts 按最长轴坐标升序（index 1 = 轴向起点）。
- 提取策略（2026-09-22 实测 3MB / 67-solid 真实模型：纯网格路径 65s 超时）：
  1. 多体（solids > 1）→ connected_components：逐 solid 取 OCCT B-rep 精确性质
     （Volume / BoundingBox，零镶嵌，实测 5.5s；extractor=cadquery-occt/brep）；
  2. 单体 → radius_profile：镶嵌 + trimesh，面质心按最长轴分 bin 取半径中位数，
     相邻有效观测的半径变化超过 max(5% 参考半径, 2×镶嵌容差) 时，在两观测 bin 中心的
     中点切分（圆柱面镶嵌的质心只出现在每段 1/3、2/3 高度，观测间隙的中点恰为真实
     变径交接面）；端盖面（|法向·轴| ≥ 0.9）不参与剖面，避免端部伪切分；碎片段
     （体积 < 1% 总体积）并入轴向邻段；
  3. 兜底 fallback_single：整体作为单段（诚实降级；整体 bbox vs 全长类参数的校验价值仍在）。
- 分段体积：B-rep 路径为 OCCT 精确体积；网格路径为面片有符号四面体和（按质心归段，
  总和与整体严格守恒）＋ 切面虚拟封盖修正 ±(1/3)·c·A——被切开的段在切面处缺盖，
  盖项用 c+δ 截面有向面积补齐（穿越三角形定向弦求和，弦方向规则保证绕向一致、
  环形截面内孔自动负值抵消；纯 numpy，零额外依赖，精确）。
- 失败绝不抛异常：CLI 捕获一切异常写 {"ok": false, "error": ...}，退出码 1（A-16 如实降级）。
- 本模块对项目内零依赖（只 import cadquery/trimesh/numpy/stdlib），保证可独立子进程运行。
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

EXTRACTOR_BREP = "cadquery-occt/brep"
EXTRACTOR_MESH = "cadquery-occt/trimesh"


# ------------------------------------------------------------------------------
def inspect_step(step_bytes: bytes) -> dict:
    """STEP 字节 → 实测几何 JSON（契约见模块 docstring）。任何失败返回 {"ok": false}，不抛异常。

    提取策略（2026-09-22 实测 3MB/67-solid 真实模型：网格路径 65s 超时，B-rep 路径 5.5s）：
    - 多体（solids > 1）：直接取 OCCT B-rep 精确性质（Volume / BoundingBox，零镶嵌）；
    - 单体 / 无 solid：镶嵌 + trimesh 半径剖面（单体融合回转体的分段仍需网格剖面）。
    """
    started = time.perf_counter()
    tmp_path: str | None = None
    try:
        import cadquery as cq  # 延迟导入：OCP 重依赖只在提取时加载

        fd, tmp_path = tempfile.mkstemp(suffix=".step", prefix="geo_inspect_")
        with os.fdopen(fd, "wb") as f:
            f.write(step_bytes)
        shape = cq.importers.importStep(tmp_path).val()
        bb = shape.BoundingBox()
        overall_bbox = [float(bb.xlen), float(bb.ylen), float(bb.zlen)]
        diag = math.sqrt(sum(v * v for v in overall_bbox)) or 1000.0
        tol = max(1.0, diag * 0.0003)  # 镶嵌容差与 convert_worker 同口径（仅网格路径用）

        try:
            solids = shape.Solids()
        except Exception:
            solids = []

        if len(solids) > 1:
            parts = _parts_from_brep_solids(solids)
            method = "connected_components"
            extractor = EXTRACTOR_BREP
            overall_volume = sum(p["volume_mm3"] for p in parts)
        else:
            parts, method, overall_bbox, overall_volume = _segment_by_mesh(shape, tol)
            extractor = EXTRACTOR_MESH

        if not parts:
            return {"ok": False, "error": "无可用几何实体（solids 为空且镶嵌结果为空网格）"}

        return {
            "ok": True,
            "overall": {
                "bbox_mm": [round(v, 4) for v in overall_bbox],
                "volume_mm3": round(float(overall_volume), 3),
                "part_count": len(parts),
            },
            "parts": _finalize(parts, overall_bbox),
            "meta": {
                "extractor": extractor,
                "tolerance_mm": round(tol, 4),
                "elapsed_s": round(time.perf_counter() - started, 3),
                "segmentation": method,
            },
        }
    except Exception as exc:  # noqa: BLE001 —— 失败如实降级，绝不阻断管线（A-14/A-16）
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _parts_from_brep_solids(solids) -> list[dict]:
    """多体模型逐 solid 取 B-rep 精确性质（OCCT GProp，无镶嵌）：体积精确、
    bbox 为 OCCT 快速包围盒（含 ~0.1mm 容差膨胀，远小于 A4 的 2% 容差口径）。"""
    parts: list[dict] = []
    for s in solids:
        try:
            bb = s.BoundingBox()
            vol = abs(float(s.Volume()))
        except Exception:
            continue  # 单个 solid 性质失败如实跳过，不影响其余
        parts.append(_make_part(
            (bb.xlen, bb.ylen, bb.zlen),
            ((bb.xmin + bb.xmax) / 2.0, (bb.ymin + bb.ymax) / 2.0, (bb.zmin + bb.zmax) / 2.0),
            vol,
        ))
    return parts


def _segment_by_mesh(shape, tol: float) -> tuple[list[dict], str, list[float], float]:
    """网格路径（单体/无 solid）：镶嵌 → trimesh → 三级降级分段。"""
    import trimesh

    verts, tris = shape.tessellate(tol)
    mesh = trimesh.Trimesh(
        vertices=[(v.x, v.y, v.z) for v in verts], faces=list(tris), process=True
    )
    if len(mesh.faces) == 0:
        return [], "fallback_single", [0.0, 0.0, 0.0], 0.0
    parts, method = _segment(mesh, tol)
    b0, b1 = mesh.bounds
    overall_bbox = [float(v) for v in (b1 - b0)]
    return parts, method, overall_bbox, abs(float(mesh.volume))


# ------------------------------------------------------------------------------
# 分段
def _split_components(mesh) -> list:
    """连通体拆分：face 邻接 + 并查集（纯 numpy）。trimesh 自带的 split() 依赖
    scipy/networkx 图引擎（均未声明安装），故自实现；每个连通体输出为独立子网格。"""
    import numpy as np

    n = len(mesh.faces)
    if n == 0:
        return []
    parent = np.arange(n)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = int(parent[x])
        return x

    for a, b in mesh.face_adjacency:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(n)])
    comps = []
    for r in np.unique(roots):
        faces = np.flatnonzero(roots == r)
        try:
            comps.append(mesh.submesh([faces], append=True))
        except Exception:
            continue
    return comps


def _segment(mesh, tol: float) -> tuple[list[dict], str]:
    import numpy as np

    total = abs(float(mesh.volume))

    # 1) 连通体拆分（未融合多体）
    try:
        components = _split_components(mesh)
    except Exception:
        components = []
    parts = []
    for comp in components:
        if abs(float(comp.volume)) <= 1e-4 * total:
            continue  # 镶嵌碎片
        parts.append(_part_from_mesh(comp))
    if len(parts) > 1:
        return _finalize(parts, [float(v) for v in (mesh.bounds[1] - mesh.bounds[0])]), "connected_components"

    # 2) 半径剖面分段（融合单 solid 的同轴变径回转体）
    b0 = mesh.bounds[0]
    ext = mesh.bounds[1] - mesh.bounds[0]
    axis = int(np.argmax(ext))
    cuts = _radius_profile_cuts(mesh, axis, tol)
    if cuts:
        segs, cuts = _assign_and_merge(mesh, axis, cuts)
        if len(segs) > 1:
            parts = _parts_by_cuts(mesh, axis, segs, cuts)
            return _finalize(parts, [float(v) for v in (mesh.bounds[1] - mesh.bounds[0])]), "radius_profile"

    # 3) 兜底整体单段
    return _finalize([_part_from_mesh(mesh)]), "fallback_single"


def _radius_profile_cuts(mesh, axis: int, tol: float) -> list[tuple[float, float, float]]:
    """半径剖面检测切点（相对轴起点，升序），返回 [(切点, 间隙左界, 间隙右界)]。

    只对「有效观测」做相邻比较（空 bin 表示无观测，不代表半径延续或变化）；
    相邻观测半径变化 > max(5% 参考半径, 2×容差) 时，切在两观测 bin 中心的中点——
    观测间隙的中点即真实变径交接面。无切点返回空。
    """
    import numpy as np

    b0 = mesh.bounds[0]
    length = float((mesh.bounds[1] - mesh.bounds[0])[axis])
    if length <= 0:
        return []

    tri = mesh.triangles
    centroids = tri.mean(axis=1)
    coords = centroids[:, axis] - float(b0[axis])
    radius = np.linalg.norm(np.delete(centroids, axis, axis=1), axis=1)
    lateral = np.abs(mesh.face_normals[:, axis]) < 0.9  # 端盖不参与半径剖面
    if int(lateral.sum()) < 8:
        lateral = np.ones(len(tri), dtype=bool)

    bin_width = max(2.0 * tol, length / 200.0)
    n_bins = int(min(200, max(20, math.ceil(length / bin_width))))
    edges = np.linspace(0.0, length, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, coords[lateral], side="right") - 1, 0, n_bins - 1)
    prof = np.full(n_bins, np.nan)
    for b in range(n_bins):
        sel = idx == b
        if sel.any():
            prof[b] = float(np.median(radius[lateral][sel]))

    obs = [(b, float(prof[b])) for b in range(n_bins) if not math.isnan(prof[b])]
    if len(obs) < 2:
        return []
    out: list[tuple[float, float, float]] = []
    prev_b, prev_v = obs[0]
    for b, v in obs[1:]:
        if abs(v - prev_v) > max(0.05 * abs(prev_v), 2.0 * tol):
            c_prev = (edges[prev_b] + edges[prev_b + 1]) / 2.0
            c_cur = (edges[b] + edges[b + 1]) / 2.0
            out.append(((c_prev + c_cur) / 2.0, c_prev, c_cur))
        prev_b, prev_v = b, v
    return out


def _assign_and_merge(mesh, axis: int, cuts: list[tuple[float, float, float]]) -> tuple[list, list[float]]:
    """按切点把面归段并做碎片段合并（体积 < 1% 总体积并入邻段）。

    归段：质心 ≤ 切点归前段；落在观测间隙内的端面（|法向·轴| ≥ 0.9）按法向归段——
    朝 +axis 的面是前段的顶面，朝 −axis 的面是后段的底面（保证段 bbox 不被交界面
    上的大半径端面撑大）。返回 (每段 face 下标数组, 剩余切点坐标)。
    """
    import numpy as np

    centroids = mesh.triangles.mean(axis=1)
    coords = centroids[:, axis] - float(mesh.bounds[0][axis])
    nrm = mesh.face_normals[:, axis]
    positions = np.asarray([c[0] for c in cuts])
    seg_ids = np.searchsorted(positions, coords, side="left")
    for i, (_pos, glo, ghi) in enumerate(cuts):
        junction = (coords > glo) & (coords <= ghi) & (np.abs(nrm) >= 0.9)
        seg_ids[junction & (nrm > 0)] = i
        seg_ids[junction & (nrm < 0)] = i + 1
    segs = [np.flatnonzero(seg_ids == k) for k in range(len(cuts) + 1)]
    segs = [s for s in segs if len(s)]

    # 段 k 的底/顶切点分别是 cuts[k-1] / cuts[k]；合并相邻段 = 删去其间切点
    thr = 0.01 * abs(float(mesh.volume))
    segs_l, pos_l = list(segs), [c[0] for c in cuts]
    while len(segs_l) > 1:
        vabs = [abs(_signed_volume(mesh, s)) for s in segs_l]
        i = int(np.argmin(vabs))
        if vabs[i] >= thr:
            break
        if i == 0:
            j = 1
        elif i == len(segs_l) - 1:
            j = i - 1
        else:
            j = i - 1 if vabs[i - 1] <= vabs[i + 1] else i + 1
        lo, hi = min(i, j), max(i, j)
        segs_l[lo:hi + 1] = [np.concatenate([segs_l[lo], segs_l[hi]])]
        del pos_l[lo]
    return segs_l, pos_l


def _parts_by_cuts(mesh, axis: int, segs: list, cuts: list[float]) -> list[dict]:
    """逐段构造 part：体积 = 面片有符号和 + 切面虚拟封盖修正（守恒：相邻段一加一减）。"""
    b0 = mesh.bounds[0]
    length = float((mesh.bounds[1] - mesh.bounds[0])[axis])
    delta = 1e-4 * length + 1e-6  # 截面采样面偏离切点一小步，避开面片顶点/共面特征
    parts = []
    for k, faces in enumerate(segs):
        vol = _signed_volume(mesh, faces)
        if k > 0:  # 底部切点 cuts[k-1]：虚拟底盖（外法向 −axis）
            c_abs = float(b0[axis] + cuts[k - 1])
            area = abs(_section_area(mesh, axis, cuts[k - 1], delta))
            vol -= c_abs * area / 3.0
        if k < len(cuts):  # 顶部切点 cuts[k]：虚拟顶盖（外法向 +axis）
            c_abs = float(b0[axis] + cuts[k])
            area = abs(_section_area(mesh, axis, cuts[k], delta))
            vol += c_abs * area / 3.0
        parts.append(_part_from_faces(mesh, faces, volume=abs(vol)))
    return parts


def _section_area(mesh, axis: int, c_rel: float, delta: float) -> float:
    """轴上 c_rel+δ 处截面的有向面积（mm²）。

    截面轮廓由穿越三角形的定向弦首尾相接构成；弦方向规则（自 d 由 + 变 − 的边指向
    d 由 − 变 + 的边）保证全部弦绕向一致——外环为正、内孔为负，环形截面自动得净面积；
    各弦首尾相接故可直接求和，无需显式链环（弦梯形公式）。
    """
    import numpy as np

    c_abs = float(mesh.bounds[0][axis]) + c_rel + delta
    tri = mesh.triangles
    d = tri[:, :, axis] - c_abs
    crossing = (d > 0).any(axis=1) & (d < 0).any(axis=1)
    if not crossing.any():
        return 0.0
    T = tri[crossing]
    D = d[crossing]

    def edge_point(i: int, j: int):
        denom = D[:, i] - D[:, j]
        t = np.divide(D[:, i], denom, out=np.zeros_like(D[:, i]), where=denom != 0)[:, None]
        return T[:, i] + t * (T[:, j] - T[:, i])

    chords = [edge_point(0, 1), edge_point(1, 2), edge_point(2, 0)]
    p_from = np.zeros((len(T), 3))
    p_to = np.zeros((len(T), 3))
    got_from = np.zeros(len(T), dtype=bool)
    got_to = np.zeros(len(T), dtype=bool)
    for (i, j), Pij in zip(((0, 1), (1, 2), (2, 0)), chords):
        di, dj = D[:, i], D[:, j]
        f = (di > 0) & (dj < 0)  # +→− 边：弦起点
        t = (di < 0) & (dj > 0)  # −→+ 边：弦终点
        p_from[f] = Pij[f]
        p_to[t] = Pij[t]
        got_from |= f
        got_to |= t
    valid = got_from & got_to
    if not valid.any():
        return 0.0
    axis_vec = np.zeros(3)
    axis_vec[axis] = 1.0
    return float(np.einsum("ij,j->i", np.cross(p_from[valid], p_to[valid]), axis_vec).sum() / 2.0)


# ------------------------------------------------------------------------------
# part 构造
def _part_from_mesh(mesh) -> dict:
    b0, b1 = mesh.bounds
    return _make_part(b1 - b0, (b0 + b1) / 2.0, abs(float(mesh.volume)))


def _part_from_faces(mesh, faces, volume: float | None = None) -> dict:
    """按 face 子集构造 part：bbox/中心取子集顶点包络；体积默认按有符号四面体累加
    （半径剖面路径传入含封盖修正的 volume）。"""
    import numpy as np

    faces = np.asarray(faces)
    used = np.unique(mesh.faces[faces])
    pts = mesh.vertices[used]
    b0, b1 = pts.min(axis=0), pts.max(axis=0)
    vol = abs(_signed_volume(mesh, faces)) if volume is None else volume
    return _make_part(b1 - b0, (b0 + b1) / 2.0, vol)


def _signed_volume(mesh, faces) -> float:
    import numpy as np

    tri = mesh.triangles[np.asarray(faces)]
    return float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)


def _make_part(bbox_mm, center_mm, volume_mm3) -> dict:
    return {
        "center_mm": [round(float(c), 4) for c in center_mm],
        "bbox_mm": [round(float(b), 4) for b in bbox_mm],
        "volume_mm3": round(float(volume_mm3), 3),
    }


def _finalize(parts: list[dict], overall_bbox: list[float] | None = None) -> list[dict]:
    """按最长轴坐标升序定序并编号（index 1 = 轴向起点）。"""
    import numpy as np

    if overall_bbox:
        axis = int(np.argmax(overall_bbox))
        parts = sorted(parts, key=lambda p: p["center_mm"][axis])
    return [
        {"index": i, "center_mm": p["center_mm"], "bbox_mm": p["bbox_mm"], "volume_mm3": p["volume_mm3"]}
        for i, p in enumerate(parts, 1)
    ]


# ------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI：python geometry_inspect.py <step_path> <out_json>。退出码 0 成功 / 1 失败 / 2 用法错误。"""
    argv = list(sys.argv if argv is None else argv)
    if len(argv) != 3:
        print("usage: python geometry_inspect.py <step_path> <out_json>", file=sys.stderr)
        return 2
    try:
        step_bytes = Path(argv[1]).read_bytes()
    except OSError as exc:
        result = {"ok": False, "error": f"读取 STEP 失败: {exc}"}
    else:
        result = inspect_step(step_bytes)
    Path(argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok", False),
                      "part_count": result.get("overall", {}).get("part_count", 0)}))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
