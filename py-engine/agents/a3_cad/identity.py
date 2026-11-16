# -*- coding: utf-8 -*-
"""A3 对象身份与显示变换声明解析（T15.1/T15.2，ADR-014）。

契约来源：ADR-014 §1/§2/§3——A3 生成的 CadQuery 脚本内嵌声明注释，随脚本缓存/
版本化（A0-3 envelope 整体缓存，无需额外 LLM 调用）；解析在**最终脚本**上执行
（修复循环可能改写脚本，中途注释丢失则以最终态为准，如实降级为无声明=legacy）。

语法（B-1/B-2，通用层，任何领域可用）：
    # @obj <object_id> role=<角色> [params=<参数id:尺寸类型,...>]
    # @display param=<参数id|none> physical_mm=<物理值mm> visual_mm=<视觉值mm> [rule=<规则>] [reason=<说明>]

- 键值内不得含空白（rule/reason 用紧凑表达式或下划线）。
- 畸形行（缺键/非法数值）跳过并 warning，不阻断管线；无分数不造值（A-16）。
- LLM 声明只是意图不是真值：@obj 声明须经 build_param_mapping 与实测几何互证后
  才能成为 verified 映射（T15.2）；@display 声明落 IR3 display_transforms 由 A4
  显性呈现（CHK-DISPLAY-TRANSFORM），不再静默缩小工程参数（A-16）。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger("sonarcad.a3.identity")

# 行头：# @display ...
_DISPLAY_LINE_RE = re.compile(r"^\s*#\s*@display\b(?P<body>.*)$")
# 行头：# @obj <object_id> ...（T15.2 使用）
_OBJ_LINE_RE = re.compile(r"^\s*#\s*@obj\b\s+(?P<oid>[^\s]+)(?P<body>.*)$")
# 键值对：key=value（value 不含空白与 #）
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s#]+)")
# params 值：param_id:dim_type 逗号分隔（T15.2 使用）
_PARAM_SPEC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-\.]*):([a-z_]+)$")

REQUIRED_DISPLAY_KEYS = ("physical_mm", "visual_mm")

# 尺寸语义类型（ADR-014 §1；v1 语义检查器实现 length/diameter）
DIM_TYPES = ("length", "diameter", "width", "height", "thickness", "other")

_MAPPING_VERSION = "1"
_TIE_EPS_PCT = 0.01   # 绑定评分并列阈值（偏差百分点；并列=无法区分个体→ambiguous）
_MAX_RANGE = 1000     # 区间语法展开上限（防手误大范围爆内存）


def parse_display_transforms(script: str) -> list[dict[str, Any]]:
    """解析脚本内全部 ``# @display`` 声明 → IR3 display_transforms 条目列表。

    - param=none 或缺省 → param_id=None（纯视觉说明，无对应 IR2 参数）。
    - physical_mm/visual_mm 必需且必须可解析为有限正/负数值（振幅可为负）；
      违规行整行跳过并 warning（A-16：缺数据不造值，不产出半截契约对象）。
    - 同一参数多条声明全部保留（如实呈现，不去重合并——多重缩放本身即异常信号，
      由审阅人工裁决）。
    """
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate((script or "").splitlines(), 1):
        m = _DISPLAY_LINE_RE.match(line)
        if not m:
            continue
        kv = dict(_KV_RE.findall(m.group("body")))
        missing = [k for k in REQUIRED_DISPLAY_KEYS if k not in kv]
        try:
            physical = float(kv.get("physical_mm", ""))
            visual = float(kv.get("visual_mm", ""))
        except ValueError:
            logger.warning(
                "A3 @display 声明第 %d 行数值非法（缺：%s），整行跳过: %s",
                lineno, ",".join(missing) or "number", line.strip(),
            )
            continue
        entry: dict[str, Any] = {"param_id": None, "physical_mm": physical, "visual_mm": visual}
        pid = kv.get("param")
        if pid and pid.lower() != "none":
            entry["param_id"] = pid
        if kv.get("rule"):
            entry["rule"] = kv["rule"]
        if kv.get("reason"):
            entry["reason"] = kv["reason"]
        out.append(entry)
    return out


# ------------------------------------------------------------------------------
# @obj 对象声明解析（T15.2，ADR-014 §2）
_RANGE_RE = re.compile(r"^(?P<prefix>.+)\[(?P<start>\d+)-(?P<end>\d+)\]$")


def _expand_object_id(raw: str) -> list[str]:
    """object_id 区间语法 name-[01-16] → name-01..name-16（零填充宽度取起点写法）。"""
    m = _RANGE_RE.match(raw)
    if not m:
        return [raw]
    start, end = int(m.group("start")), int(m.group("end"))
    if end < start or end - start + 1 > _MAX_RANGE:
        return []
    return [f"{m.group('prefix')}{str(i).zfill(len(m.group('start')))}" for i in range(start, end + 1)]


def parse_object_declarations(script: str) -> list[dict[str, Any]]:
    """解析脚本内全部 ``# @obj`` 声明（T15.2）→ [{"object_id","role","params":[...]}]。

    - params 段语法 ``p1:diameter,p2:length``；dim_type 不在 DIM_TYPES 或缺冒号 →
      该参数段跳过并 warning（其余保留）；
    - 缺 role / 区间语法非法 → 整行跳过；object_id 重复 → 保留首个并 warning；
    - 解析在最终脚本上执行（修复循环可能改写；声明丢失=无映射，legacy 如实）。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineno, line in enumerate((script or "").splitlines(), 1):
        m = _OBJ_LINE_RE.match(line)
        if not m:
            continue
        kv = dict(_KV_RE.findall(m.group("body")))
        role = kv.get("role")
        if not role:
            logger.warning("A3 @obj 声明第 %d 行缺 role，整行跳过: %s", lineno, line.strip())
            continue
        ids = _expand_object_id(m.group("oid"))
        if not ids:
            logger.warning("A3 @obj 声明第 %d 行区间语法非法，整行跳过: %s", lineno, line.strip())
            continue
        specs: list[dict[str, str]] = []
        for spec in (kv.get("params") or "").split(","):
            spec = spec.strip()
            if not spec:
                continue
            sm = _PARAM_SPEC_RE.match(spec)
            if not sm or sm.group(2) not in DIM_TYPES:
                logger.warning("A3 @obj 声明第 %d 行参数段非法已跳过: %s", lineno, spec)
                continue
            specs.append({"param_id": sm.group(1), "dim_type": sm.group(2)})
        for oid in ids:
            if oid in seen:
                logger.warning("A3 @obj 声明 object_id 重复，保留首个: %s", oid)
                continue
            seen.add(oid)
            out.append({"object_id": oid, "role": role,
                        "params": [dict(s) for s in specs]})
    return out


# ------------------------------------------------------------------------------
# 声明 + 几何互证（T15.2，ADR-014 §2）

def _part_measurements(part: dict[str, Any]) -> dict[str, list[tuple[str, float]]]:
    """单段的尺寸语义测量证据（mm）。diameter 只认 radius_derived（圆度证据半径×2，
    不做 bbox 启发式——同值宽度不可顶替孔径，案例 03 硬前提）；length 优先测量轴
    （radius_profile/圆柱面轴向），兜底最长轴（弯曲段弧长≠bbox 投影的局限由 basis 如实）；
    thickness 由均匀环形圆柱推导（T15.5，ADR-014 §7）：R=最大圆柱面半径（radii_mm 外
    表面）、r_in=sqrt(R²−V/(πL))，且 r_in 须与某个 <R 的真实圆柱面半径对上（≤1% 互证
    自证）——实心杆（r_in≈0）、阶梯/法兰轴（推导内径对不上任何真实内孔面）、网格路径
    （无 radii_mm）如实不产证据（A-16 不造值；假阴性方向偏置=宁可 unverified）。"""
    bbox = part.get("bbox_mm") or []
    out: dict[str, list[tuple[str, float]]] = {}
    if len(bbox) == 3 and all(isinstance(b, (int, float)) and b > 0 for b in bbox):
        lengths: list[tuple[str, float]] = []
        axis = part.get("axis")
        if axis in (0, 1, 2):
            lengths.append(("bbox_axis", float(bbox[axis])))
        lengths.append(("bbox_axis_longest", max(float(b) for b in bbox)))
        out["length"] = lengths
        r = part.get("radius_mm")
        if isinstance(r, (int, float)) and r > 0:
            out["diameter"] = [("radius_derived", 2.0 * float(r))]
        radii = part.get("radii_mm")
        vol = part.get("volume_mm3")
        if (isinstance(radii, list) and len(radii) >= 2
                and isinstance(vol, (int, float)) and vol > 0 and axis in (0, 1, 2)):
            r_out = max(float(v) for v in radii if isinstance(v, (int, float)) and v > 0)
            if r_out > 0:
                inner_sq = r_out * r_out - float(vol) / (math.pi * float(bbox[axis]))
                if inner_sq > 0:
                    r_in = math.sqrt(inner_sq)
                    bores = [float(v) for v in radii
                             if isinstance(v, (int, float)) and 0 < v < r_out]
                    # 内孔互证自证：推导内径必须与真实内孔圆柱面半径对上（≤1%），
                    # 否则截面非均匀环形，推导值不可信（如阶梯轴），不产证据
                    if bores and any(abs(r_in - b) <= 0.01 * r_in for b in bores):
                        out["thickness"] = [("wall_derived", round(r_out - r_in, 4))]
    return out


def build_param_mapping(
    decls: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    params: list[dict[str, Any]],
    tol_pct: float,
) -> dict[str, Any] | None:
    """声明 + 几何互证 → IR3 param_mapping（无声明/无分段返回 None，调用方省略字段）。

    绑定语义（关键设计，案例 02/03/04 的机制前提）：
    - 声明是身份的**结构锚**（构造处意图），不是真值（DEV-001 §2.1 前置 2）；绑定按
      「声明尺寸期望 vs 实测」最优一致性评分做一一分配（最确信优先，一段一对象），
      **不以数值相近为门槛**——超差绑定保留并进测量记录，由 A4 语义检查器按检查容差
      报 error（案例 02：100 要求 vs 150 实测必报超差，而非 unverified）。
    - 评分并列（同分 ≤0.01pp）→ ambiguous（无法区分个体，如实）；证据段不足/剩余段
      数不齐 → 按 ADR-014 §2 四态语义落 ambiguous/unmatched。
    - diameter 证据缺失的段不能作为带孔径声明对象的证据候选（不造直径测量）。
    - 无可验证尺寸的声明（role-only）按剩余段消除法绑定：唯一剩余 → geometry_unique；
      多余/不足 → ambiguous（不假装挑选）；role-only 无检查语义。
    - tol_pct 仅用于记录语义（绑定本身不设阈值）；保留参数以对齐 legacy 联动口径。
    """
    if not decls or not parts:
        return None
    from agents.a3_cad.geometry_link import length_params_mm

    expected_mm = {p["id"]: v for p, v in length_params_mm(params)}
    pmeas = [_part_measurements(p) for p in parts]

    def spec_verifiable(spec: dict[str, str]) -> bool:
        return (spec["dim_type"] in ("length", "diameter", "thickness")
                and spec["param_id"] in expected_mm)

    def score(specs: list[dict[str, str]], pi: int) -> float | None:
        """段 pi 对声明 specs 的证据评分（各可验证维度最差偏差，pp）；任一可验证维度
        无测量证据 → None（该段不能作为证据候选）。"""
        devs: list[float] = []
        for spec in specs:
            if not spec_verifiable(spec):
                continue
            cands = pmeas[pi].get(spec["dim_type"]) or []
            if not cands:
                return None
            exp = expected_mm[spec["param_id"]]
            devs.append(min(abs(mm - exp) / exp * 100.0 for _b, mm in cands))
        return max(devs) if devs else None

    # 类分组：同 role+同参数集 = 对称类（类内个体不假装区分）；保持首现顺序
    classes: list[dict[str, Any]] = []
    by_key: dict[tuple, dict[str, Any]] = {}
    for d in decls:
        key = (d["role"], tuple((s["param_id"], s["dim_type"]) for s in d["params"]))
        if key not in by_key:
            by_key[key] = {
                "role": d["role"], "specs": list(d["params"]), "members": [],
                "verifiable": any(spec_verifiable(s) for s in d["params"]),
            }
            classes.append(by_key[key])
        by_key[key]["members"].append(d["object_id"])

    binding: dict[str, int] = {}                      # object_id -> part 下标
    status_of: dict[str, tuple[str, str | None]] = {}  # object_id -> (status, basis)
    class_size: dict[str, int] = {}                   # object_id -> 所属类实例数

    # 处理顺序：有可验证尺寸的类优先（约束力更强），类间按声明首现顺序
    classes.sort(key=lambda c: (not c["verifiable"], c["members"][0]))
    available = set(range(len(parts)))

    for cls in classes:
        ids = cls["members"]
        n = len(ids)
        for oid in ids:
            class_size[oid] = n
        if cls["verifiable"]:
            scored = sorted(
                ((score(cls["specs"], pi), pi) for pi in available),
                key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0, parts[t[1]]["index"]),
            )
            evidence = [t for t in scored if t[0] is not None]
            if len(evidence) > n and abs(evidence[n - 1][0] - evidence[n][0]) <= _TIE_EPS_PCT:
                # 第 n 与第 n+1 证据候选同分：类内无法区分个体（如实歧义）
                for oid in ids:
                    status_of[oid] = ("ambiguous", None)
                continue
            take = evidence[:n]
            for oid, (_s, pi) in zip(ids, take):
                binding[oid] = pi
                available.discard(pi)
                status_of[oid] = ("verified", "symmetric_class" if n > 1 else "declared_tag")
            for oid in ids[len(take):]:
                status_of[oid] = ("unmatched", None)
        else:
            rest = sorted(available, key=lambda pi: parts[pi]["index"])
            if len(rest) == n:
                for oid, pi in zip(ids, rest):
                    binding[oid] = pi
                    available.discard(pi)
                    status_of[oid] = ("verified", "symmetric_class" if n > 1 else "geometry_unique")
            elif len(rest) > n:
                for oid in ids:  # 剩余段多于声明：无法唯一划归，不假装挑选
                    status_of[oid] = ("ambiguous", None)
            else:
                for oid in ids:
                    status_of[oid] = ("unmatched", None)

    objects: list[dict[str, Any]] = []
    for d in decls:
        oid = d["object_id"]
        status, basis = status_of.get(oid, ("unmatched", None))
        pi = binding.get(oid)
        params_out: list[dict[str, Any]] = []
        for s in d["params"]:
            entry: dict[str, Any] = {"param_id": s["param_id"], "dim_type": s["dim_type"],
                                     "measured_mm": None, "measurement": "none"}
            if status == "verified" and pi is not None:
                cands = pmeas[pi].get(s["dim_type"]) or []
                if cands:
                    exp = expected_mm.get(s["param_id"])
                    if exp is not None and exp > 0:
                        b, mm = min(cands, key=lambda c: abs(c[1] - exp))
                    else:
                        b, mm = cands[0]
                    entry["measured_mm"] = round(mm, 4)
                    entry["measurement"] = b
            params_out.append(entry)
        obj: dict[str, Any] = {
            "object_id": oid, "role": d["role"],
            "feature_id": f"F-{parts[pi]['index']:03d}" if pi is not None else None,
            "status": status, "params": params_out,
        }
        if status == "verified":
            obj["basis"] = basis
            if class_size.get(oid, 1) > 1:
                obj["symmetric_size"] = class_size[oid]
        objects.append(obj)

    return {"version": _MAPPING_VERSION, "identity_basis": "declared_objects", "objects": objects}


# ------------------------------------------------------------------------------
# legacy 几何候选身份（T15.4，ADR-014 §6）——无声明模型（旧 STEP）的诚实匹配

def build_candidate_mapping(
    params: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    tol_pct: float,
) -> dict[str, Any] | None:
    """无声明（旧 STEP/legacy）模型 → 几何候选身份映射（T15.4，ADR-014 §6）。

    DEV-001 §2.1 前置 3：旧 STEP 使用几何/拓扑候选匹配但允许不确定。无声明结构锚时
    身份只能来自几何证据本身，故 verified 仅在唯一最优候选时给出，并列必显性（歧义
    必须可见——同为值邻近贪心，与 link_params 的差别在状态学：静默吞并列 → 四态如实）：

    - 候选证据与声明制同源（_part_measurements）：length=bbox 测量轴/最长轴；
      diameter=radius_derived（圆度证据，同值宽度不可顶替，案例 03 口径不变）；
    - 同段同参同中 diameter 与 length（圆柱横维与 2r 数值重合）→ 取 radius_derived
      （更具体的圆度证据；measurement 如实记录，测量值无差异无造值）；
    - 唯一最优候选（首2偏差差 >并列阈）且该段未被更优参数占用 → verified
      （basis=geometry_candidate）；首2候选并列 → ambiguous（不假装挑选）；
      候选全被更优参数占用 → unmatched（有候选但身份让位，如实）；无候选 → 不进映射
      （legacy CHK-GEO-NUM/SKIP 照旧裁决，不重复不遗漏）。

    tol_pct ≤ 0 视为关闭；无长度类参数或无候选对象 → None（调用方省略字段）。
    """
    if tol_pct <= 0 or not params or not parts:
        return None
    from agents.a3_cad.geometry_link import length_params_mm

    expected = {str(p["id"]): v for p, v in length_params_mm(params)}
    pmeas = [_part_measurements(p) for p in parts]

    # 每参数的容差内候选证据（按段去重：同段 diameter 优先于 length）
    cands_by_param: dict[str, list[dict[str, Any]]] = {}
    for pid, exp in expected.items():
        cands: list[dict[str, Any]] = []
        for pi, meas in enumerate(pmeas):
            dia = [c for _b, c in (meas.get("diameter") or [])
                   if exp > 0 and abs(c - exp) / exp * 100.0 <= tol_pct]
            if dia:
                mm = min(dia, key=lambda c: abs(c - exp))
                cands.append({"pi": pi, "dev": abs(mm - exp) / exp * 100.0,
                              "dim_type": "diameter", "basis": "radius_derived", "mm": mm})
                continue
            len_c = [(b, c) for b, c in (meas.get("length") or [])
                     if exp > 0 and abs(c - exp) / exp * 100.0 <= tol_pct]
            if len_c:
                b, mm = min(len_c, key=lambda bc: abs(bc[1] - exp))
                cands.append({"pi": pi, "dev": abs(mm - exp) / exp * 100.0,
                              "dim_type": "length", "basis": b, "mm": mm})
        cands.sort(key=lambda c: (c["dev"], parts[c["pi"]]["index"]))
        if cands:
            cands_by_param[pid] = cands
    if not cands_by_param:
        return None

    # 绑定：仅唯一最优者参与贪心占段（并列者 ambiguous，不占段不假装）
    binding: dict[str, dict[str, Any]] = {}
    taken: set[int] = set()
    clear: list[tuple[float, str, dict[str, Any]]] = []
    for pid, cands in cands_by_param.items():
        if len(cands) >= 2 and cands[1]["dev"] - cands[0]["dev"] <= _TIE_EPS_PCT:
            continue
        clear.append((cands[0]["dev"], pid, cands[0]))
    clear.sort(key=lambda t: (t[0], parts[t[2]["pi"]]["index"]))
    for _dev, pid, best in clear:
        if best["pi"] in taken:
            continue  # 段被更优参数占用 → unmatched
        taken.add(best["pi"])
        binding[pid] = best

    objects: list[dict[str, Any]] = []
    for p, _v in length_params_mm(params):
        pid = str(p["id"])
        cands = cands_by_param.get(pid)
        if not cands:
            continue
        best = cands[0]
        bound = binding.get(pid)
        status = "verified" if bound else (
            "ambiguous" if (len(cands) >= 2 and cands[1]["dev"] - cands[0]["dev"] <= _TIE_EPS_PCT)
            else "unmatched")
        params_out = [{
            "param_id": pid, "dim_type": best["dim_type"],
            "measured_mm": round(bound["mm"], 4) if bound else None,
            "measurement": bound["basis"] if bound else "none",
        }]
        obj: dict[str, Any] = {
            "object_id": pid, "role": "legacy_param",
            "feature_id": f"F-{parts[bound['pi']]['index']:03d}" if bound else None,
            "status": status, "params": params_out,
        }
        if bound:
            obj["basis"] = "geometry_candidate"
        objects.append(obj)

    if not objects:
        return None
    return {"version": _MAPPING_VERSION, "identity_basis": "geometry_candidates", "objects": objects}
