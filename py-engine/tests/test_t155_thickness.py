# -*- coding: utf-8 -*-
"""T15.5 壁厚规则族 + 按参数登记容差（PHASE-015 §3.10 / ADR-014 v1.2 §7，B-4b）。

覆盖面：
- WP1 检查器：thickness 注册即用（复用长度族实现），pass/violation/unverified 诚实路径；
- WP1 按参数登记容差：PARAM_TOL_PCT 解析链（param > dim_type > 全局）+ 出处披露（A-16）；
- WP2 wall_derived 测量证据：_part_measurements 厚度推导（均匀环形圆柱模型 +
  内孔圆柱面互证自证——实心/阶梯轴/网格路径如实不产证据）与声明制映射消费。

夹具纯 dict（无 cadquery），模式与 test_t152/test_t153 一致。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a3_cad.identity import _part_measurements, build_param_mapping
from agents.a4_validation import semantics
from agents.a4_validation.agent import A4Agent

STEP_REF = {"bucket": "step-models", "object_key": "s/x.step"}


# ------------------------------------------------------------------------------
# 夹具（与 test_t153 同款纯 dict 微缩样件）
def _param(pid, value, unit="mm", name=None):
    return {"id": pid, "name": name or pid, "value": value, "unit": unit, "source_type": "case",
            "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


def _feature(fid, linked=None, dim=None):
    geom = {"bbox_mm": [10.0, 10.0, 100.0], "volume_mm3": 1.0, "center_mm": [0, 0, 0]}
    if dim is not None:
        geom["linked_dim"] = dim
    return {"feature_id": fid, "segment": "SEG-01", "geometry": geom, "linked_param_id": linked}


def _object(oid, status, params, fid="F-001", basis="declared_tag"):
    o = {"object_id": oid, "role": "tube", "feature_id": fid, "status": status, "params": params}
    if status == "verified":
        o["basis"] = basis
    return o


def _entry(pid, dim, measured, measurement):
    return {"param_id": pid, "dim_type": dim, "measured_mm": measured, "measurement": measurement}


def _ir2(*params):
    return {"design_document": "d", "parameters": list(params)}


def _ir3(objects, features=None, extracted=False):
    summary = ({"source": "extracted", "bbox_mm": [1, 1, 1], "volume_mm3": 1.0,
                "part_count": 1, "detail": "connected_components · cadquery-occt/brep"}
               if extracted else {"source": "extraction_failed", "detail": "x"})
    ir3 = {"cadquery_script": "import math\n", "step_file": STEP_REF,
           "features": features or [], "geometry_summary": summary}
    if objects is not None:
        ir3["param_mapping"] = {"version": "1", "identity_basis": "declared_objects",
                                "objects": objects}
    return ir3


def _sem_issues(ir4):
    return [i for i in ir4["issues"] if i["constraint_id"].startswith("CHK-SEM-")]


# ------------------------------------------------------------------------------
# WP1：壁厚检查器（复用长度族实现，实测=wall_derived 证据）
class TestThicknessChecker:
    def test_thickness_pass_is_info(self):
        objs = [_object("tube", "verified", [_entry("P-T", "thickness", 3.0, "wall_derived")])]
        # extracted=True：提取成功口径，legacy 无 CHK-GEO-NUM-SKIP，overall 才可能是纯 pass
        ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs, extracted=True))
        sem = _sem_issues(ir4)
        assert sem[0]["constraint_id"] == "CHK-SEM-THICKNESS"
        assert sem[0]["severity"] == "info"
        assert "wall_derived" in sem[0]["message"]
        assert ir4["overall"] == "pass"

    def test_thickness_violation_is_error_back_to_a3(self):
        # 3mm 壁厚实测 5mm：偏差 66.7% >> 全局 2% —— 如实报超差（A-16 不修饰）
        objs = [_object("tube", "verified", [_entry("P-T", "thickness", 5.0, "wall_derived")])]
        ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["constraint_id"] == "CHK-SEM-THICKNESS"
        assert sem[0]["severity"] == "error"
        assert sem[0]["suggestion"] == "back_to_A3"
        assert ir4["overall"] == "fail"

    def test_thickness_no_measurement_is_unverified(self):
        # 无 wall_derived 证据（如实不产）→ unverified，绝不以猜测凑 pass/violation
        objs = [_object("tube", "verified", [_entry("P-T", "thickness", None, "none")])]
        ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["constraint_id"] == "CHK-SEM-THICKNESS"
        assert sem[0]["severity"] == "unverified"
        assert "映射无可靠实测证据" in sem[0]["message"]
        assert ir4["overall"] == "incomplete"


# ------------------------------------------------------------------------------
# WP1：按参数登记容差（param > dim_type > 全局；出处披露 A-16）
class TestParamToleranceRegistry:
    def test_registered_tolerance_widens_verdict(self):
        # 3mm 壁厚 5% 偏差：全局 2% 报超差；按参数登记 10% 后如实放行且消息披露出处
        objs = [_object("tube", "verified", [_entry("P-T", "thickness", 3.15, "wall_derived")])]
        ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
        assert _sem_issues(ir4)[0]["severity"] == "error"
        semantics.register_param_tolerance("P-T", 10.0)
        try:
            ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
            sem = _sem_issues(ir4)
            assert sem[0]["severity"] == "info"
            assert "按参数登记" in sem[0]["message"]
        finally:
            semantics.PARAM_TOL_PCT.pop("P-T", None)

    def test_unregistered_keeps_global_message_untouched(self):
        # 未登记参数：全局默认路径，消息逐字节维持原口径（无出处后缀）
        objs = [_object("tube", "verified", [_entry("P-T", "thickness", 3.15, "wall_derived")])]
        ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["severity"] == "error"
        assert "（容差 2%）" in sem[0]["message"]
        assert "按参数登记" not in sem[0]["message"]

    def test_param_overrides_dim_type(self):
        semantics.DIM_TOL_PCT["thickness"] = 8.0
        semantics.register_param_tolerance("P-T", 3.0)
        try:
            objs = [_object("tube", "verified",
                            [_entry("P-T", "thickness", 3.15, "wall_derived")])]  # 5% 偏差
            ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
            assert _sem_issues(ir4)[0]["severity"] == "error"  # 参数级 3% < 5%，压过 dim 级 8%
            semantics.PARAM_TOL_PCT.pop("P-T")
            ir4 = A4Agent().run(_ir2(_param("P-T", 3)), _ir3(objs))
            sem = _sem_issues(ir4)
            assert sem[0]["severity"] == "info"  # 退到 dim 级 8% > 5%
            assert "dim_type 缺省" in sem[0]["message"]
        finally:
            semantics.PARAM_TOL_PCT.pop("P-T", None)
            semantics.DIM_TOL_PCT["thickness"] = None

    def test_resolution_chain_direct(self):
        semantics.register_param_tolerance("P-X", 7.5)
        semantics.DIM_TOL_PCT["thickness"] = 9.0
        try:
            assert semantics.resolve_tolerance("thickness", 2.0, "P-X") == 7.5
            assert semantics.resolve_tolerance("thickness", 2.0, "P-Y") == 9.0
            assert semantics.resolve_tolerance("length", 2.0, "P-X") == 7.5  # 参数登记跨 dim 生效
            assert semantics.resolve_tolerance("diameter", 2.0, "P-Y") == 2.0
            assert semantics.tolerance_source("thickness", "P-X") == "param"
            assert semantics.tolerance_source("thickness", "P-Y") == "dim"
            assert semantics.tolerance_source("diameter", "P-Y") == "global"
        finally:
            semantics.PARAM_TOL_PCT.pop("P-X", None)
            semantics.DIM_TOL_PCT["thickness"] = None


# ------------------------------------------------------------------------------
# WP2：wall_derived 测量证据（推导 + 内孔面互证自证，ADR-014 §7）
def _part(index, bbox, vol, radius=None, axis=None, radii=None):
    p = {"index": index, "center_mm": [0.0, 0.0, 0.0], "bbox_mm": list(bbox),
         "volume_mm3": vol}
    if radius is not None:
        p["radius_mm"] = radius
    if axis is not None:
        p["axis"] = axis
    if radii is not None:
        p["radii_mm"] = radii
    return p


# 拖曳样件声阵段同款：外径 27 / 壁厚 3 / 长 24000 的均匀环形圆柱
TUBE = _part(2, [27.0, 27.0, 24000.0], math.pi * (13.5 ** 2 - 10.5 ** 2) * 24000.0,
             radius=13.5, axis=2, radii=[10.5, 13.5])
# 实心前导缆同款：外径 20 / 长 2000 的实心圆柱（单一圆柱面）
LEAD = _part(1, [20.0, 20.0, 2000.0], math.pi * 100.0 * 2000.0, radius=10.0, axis=2,
             radii=[10.0])
# 阶梯法兰实心轴：两级半径（15×10 + 10×90），推导内径对不上任何真实内孔面
STEPPED = _part(3, [30.0, 30.0, 100.0], math.pi * (15.0 ** 2 * 10.0 + 10.0 ** 2 * 90.0),
                radius=12.5, axis=2, radii=[10.0, 15.0])


class TestWallDerivedEvidence:
    def test_annular_tube_yields_thickness_evidence(self):
        meas = _part_measurements(TUBE)
        assert meas["thickness"] == [("wall_derived", 3.0)]
        # 存量证据口径不回潮（radius_mm 中位/diameter×2/length 轴向不变）
        assert meas["diameter"] == [("radius_derived", 27.0)]
        assert ("bbox_axis", 24000.0) in meas["length"]

    def test_solid_cylinder_yields_no_thickness(self):
        assert "thickness" not in _part_measurements(LEAD)

    def test_stepped_shaft_rejected_by_corroboration(self):
        # 推导内径 10.61 对不上真实内孔面（阶梯实心轴无内孔）→ 如实不产证据
        assert "thickness" not in _part_measurements(STEPPED)

    def test_mesh_path_without_radii_yields_no_thickness(self):
        # 网格路径 parts 无 radii_mm（如实缺省）→ 无壁厚证据
        mesh_tube = _part(2, [27.0, 27.0, 24000.0], math.pi * 72.0 * 24000.0,
                          radius=13.5, axis=2)
        assert "thickness" not in _part_measurements(mesh_tube)

    def test_declared_thickness_binds_verified(self):
        decls = [{"object_id": "array_section", "role": "array_section",
                  "params": [{"param_id": "P-001", "dim_type": "diameter"},
                             {"param_id": "P-002", "dim_type": "thickness"},
                             {"param_id": "P-004", "dim_type": "length"}]}]
        params = [_param("P-001", 27), _param("P-002", 3), _param("P-004", 24, "m")]
        mapping = build_param_mapping(decls, [TUBE], params, 20.0)
        assert mapping["identity_basis"] == "declared_objects"
        obj = mapping["objects"][0]
        assert obj["status"] == "verified" and obj["basis"] == "declared_tag"
        entry = next(p for p in obj["params"] if p["param_id"] == "P-002")
        assert entry["dim_type"] == "thickness"
        assert entry["measured_mm"] == 3.0
        assert entry["measurement"] == "wall_derived"

    def test_thickness_evidence_sharpens_binding(self):
        # 同一声明下实心段因缺厚度证据不能做候选——壁厚把绑定从"数值竞争"变为唯一
        decls = [{"object_id": "array_section", "role": "array_section",
                  "params": [{"param_id": "P-001", "dim_type": "diameter"},
                             {"param_id": "P-002", "dim_type": "thickness"},
                             {"param_id": "P-004", "dim_type": "length"}]}]
        params = [_param("P-001", 27), _param("P-002", 3), _param("P-004", 24, "m")]
        mapping = build_param_mapping(decls, [LEAD, TUBE], params, 20.0)
        obj = mapping["objects"][0]
        assert obj["status"] == "verified"
        assert obj["feature_id"] == "F-002"  # 绑定管段而非实心段

    def test_solid_only_parts_unmatched(self):
        # 全实心段无可信壁厚证据 → 壁厚声明 unmatched（不假装挑选）
        decls = [{"object_id": "wall_obj", "role": "tube_wall",
                  "params": [{"param_id": "P-002", "dim_type": "thickness"}]}]
        mapping = build_param_mapping(decls, [LEAD], [_param("P-002", 3)], 20.0)
        obj = mapping["objects"][0]
        assert obj["status"] == "unmatched"
        assert obj["params"][0]["measured_mm"] is None

    def test_non_length_thickness_param_role_only(self):
        # 壁厚参数非长度单位（不进 expected_mm）→ 不可验证 → role-only 消除法绑定
        decls = [{"object_id": "w", "role": "tube_wall",
                  "params": [{"param_id": "P-T", "dim_type": "thickness"}]}]
        mapping = build_param_mapping(decls, [LEAD], [_param("P-T", 3, "dB")], 20.0)
        obj = mapping["objects"][0]
        assert obj["status"] == "verified" and obj["basis"] == "geometry_unique"
        assert obj["params"][0]["measured_mm"] is None
