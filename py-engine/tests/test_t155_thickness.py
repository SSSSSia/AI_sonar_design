# -*- coding: utf-8 -*-
"""T15.5 壁厚规则族 + 按参数登记容差（PHASE-015 §3.10 / ADR-014 v1.2 §7，B-4b）。

覆盖面：
- WP1 检查器：thickness 注册即用（复用长度族实现），pass/violation/unverified 诚实路径；
- WP1 按参数登记容差：PARAM_TOL_PCT 解析链（param > dim_type > 全局）+ 出处披露（A-16）；

夹具纯 dict（无 cadquery），模式与 test_t152/test_t153 一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


