# -*- coding: utf-8 -*-
"""T15.3 首批语义检查器单测（PHASE-015，ADR-014 §4）——审查案例 02/03 的专项判定。

- 案例 02：100 要求 vs 150 实测 → CHK-SEM-LENGTH error（必报超差，不因 >20% 断链而 unverified）；
- 案例 03：孔径按声明对象实测直径核对，同值宽度不可替代（radius_derived 证据）；
- 注册接口：register_checker 自定义 dim_type；容差按 dim_type 覆盖；
- 诚实语义：无测量/单位非长度/无检查器/参数缺失 → unverified；
- legacy 交互：被映射覆盖参数从 CHK-GEO-NUM 排除（不双重裁决、coverage 不重复计数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a4_validation import semantics
from agents.a4_validation.agent import A4Agent

STEP_REF = {"bucket": "step-models", "object_key": "s/x.step"}


def _param(pid, value, unit="mm", name=None):
    return {"id": pid, "name": name or pid, "value": value, "unit": unit, "source_type": "case",
            "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


def _feature(fid, linked=None, dim=None):
    geom = {"bbox_mm": [10.0, 10.0, 100.0], "volume_mm3": 1.0, "center_mm": [0, 0, 0]}
    if dim is not None:
        geom["linked_dim"] = dim
    return {"feature_id": fid, "segment": "SEG-01", "geometry": geom, "linked_param_id": linked}


def _object(oid, status, params, fid="F-001", basis="declared_tag"):
    o = {"object_id": oid, "role": "rod", "feature_id": fid, "status": status, "params": params}
    if status == "verified":
        o["basis"] = basis
    return o


def _entry(pid, dim, measured, measurement):
    return {"param_id": pid, "dim_type": dim, "measured_mm": measured, "measurement": measurement}


def _ir2(*params):
    return {"design_document": "d", "parameters": list(params)}


def _ir3(objects, features=None, extracted=False):
    summary = ({"source": "extracted", "bbox_mm": [10.0, 10.0, 100.0], "volume_mm3": 1.0,
                "part_count": len(features or []) or 1, "detail": "单测构造"}
               if extracted else
               {"source": "extraction_failed", "detail": "单测构造"})
    ir3 = {"cadquery_script": "import math\n", "step_file": STEP_REF,
           "features": features or [], "geometry_summary": summary}
    if objects is not None:
        ir3["param_mapping"] = {"version": "1", "identity_basis": "declared_objects",
                                "objects": objects}
    return ir3


def _sem_issues(ir4):
    return [i for i in ir4["issues"] if i["constraint_id"].startswith("CHK-SEM-")]


# ------------------------------------------------------------------------------
# 案例 02：100 要求 vs 150 实测必报超差
class TestCase02OverTolerance:
    def test_over_tolerance_is_error_back_to_a3(self):
        objs = [_object("rod", "verified", [_entry("P-L", "length", 150.0, "bbox_axis")])]
        ir4 = A4Agent().run(_ir2(_param("P-L", 100)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert len(sem) == 1
        assert sem[0]["constraint_id"] == "CHK-SEM-LENGTH"
        assert sem[0]["severity"] == "error"
        assert sem[0]["suggestion"] == "back_to_A3"
        assert "150" in sem[0]["message"] and "50.0%" in sem[0]["message"]
        assert ir4["overall"] == "fail"

    def test_legacy_would_skip_but_semantic_reports(self):
        """对照机制：同一 100 vs 150 在 legacy 联动下 >20% 不断链——语义检查器独立于
        联动容差工作（映射来自声明，非数值就近）。"""
        objs = [_object("rod", "verified", [_entry("P-L", "length", 150.0, "bbox_axis")])]
        ir3 = _ir3(objs, features=[_feature("F-001")], extracted=True)
        ir4 = A4Agent().run(_ir2(_param("P-L", 100)), ir3)
        codes = [i["constraint_id"] for i in ir4["issues"]]
        assert "CHK-GEO-NUM-SKIP" not in codes  # 无联动也无 skip（映射链路接管）
        assert "CHK-SEM-LENGTH" in codes

    def test_within_tolerance_is_info_and_pass(self):
        objs = [_object("rod", "verified", [_entry("P-L", "length", 100.5, "bbox_axis")])]
        ir4 = A4Agent().run(_ir2(_param("P-L", 100)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["severity"] == "info" and ir4["overall"] in ("pass", "incomplete")
        assert ir4["csr"] is not None and ir4["csr"] == 100.0


# ------------------------------------------------------------------------------
# 案例 03：孔径不被同值宽度替代
class TestCase03Aperture:
    def test_aperture_measured_from_radius_evidence(self):
        """孔径 100 声明在圆柱对象：实测 2r=80（radius_derived）→ error；宽度件（无
        圆度证据）在 T15.2 绑定层已不能顶替（test_t152），此处验证检查器只认证据测量。"""
        objs = [_object("elem", "verified",
                        [_entry("P-APER", "diameter", 80.0, "radius_derived")], fid="F-002")]
        ir4 = A4Agent().run(_ir2(_param("P-APER", 100)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["constraint_id"] == "CHK-SEM-DIAMETER"
        assert sem[0]["severity"] == "error"
        assert "radius_derived" in sem[0]["message"]

    def test_aperture_match_is_info(self):
        objs = [_object("elem", "verified",
                        [_entry("P-APER", "diameter", 100.0, "radius_derived")])]
        ir4 = A4Agent().run(_ir2(_param("P-APER", 100)), _ir3(objs))
        assert _sem_issues(ir4)[0]["severity"] == "info"

    def test_no_radius_evidence_is_unverified_not_pass(self):
        """无圆度证据（measurement=none）→ unverified，绝不以 bbox 宽度凑孔径判定。"""
        objs = [_object("elem", "verified", [_entry("P-APER", "diameter", None, "none")])]
        ir4 = A4Agent().run(_ir2(_param("P-APER", 100)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["severity"] == "unverified"
        assert ir4["overall"] == "incomplete"


# ------------------------------------------------------------------------------
# 诚实语义与注册接口
class TestHonestyAndRegistry:
    def test_unregistered_dim_type_is_unverified(self):
        # T15.5 后 thickness 已注册（CHK-SEM-THICKNESS），诚实降级路径由枚举中
        # 永未注册的 other 维钉住（语义不变：无检查器 → unverified 显性）
        objs = [_object("misc", "verified", [_entry("P-O", "other", 5.0, "none")])]
        ir4 = A4Agent().run(_ir2(_param("P-O", 5)), _ir3(objs))
        sem = _sem_issues(ir4)
        assert sem[0]["constraint_id"] == "CHK-SEM-OTHER"
        assert sem[0]["severity"] == "unverified"
        assert "尚无注册检查器" in sem[0]["message"]

    def test_non_length_unit_is_unverified(self):
        objs = [_object("r", "verified", [_entry("P-X", "length", 100.0, "bbox_axis")])]
        ir4 = A4Agent().run(_ir2(_param("P-X", 12, "dB")), _ir3(objs))
        assert _sem_issues(ir4)[0]["severity"] == "unverified"

    def test_dangling_declared_param_is_unverified(self):
        objs = [_object("r", "verified", [_entry("P-GONE", "length", 100.0, "bbox_axis")])]
        ir4 = A4Agent().run(_ir2(_param("P-OTHER", 1)), _ir3(objs))
        assert _sem_issues(ir4)[0]["severity"] == "unverified"

    def test_unit_conversion_m(self):
        objs = [_object("r", "verified", [_entry("P-L", "length", 1500.0, "bbox_axis")])]
        ir4 = A4Agent().run(_ir2(_param("P-L", 1.5, "m")), _ir3(objs))
        assert _sem_issues(ir4)[0]["severity"] == "info"

    def test_custom_dim_type_registration(self):
        def _span_checker(param, entry, tol_pct):
            return {"verdict": "violation", "measured_mm": entry["measured_mm"],
                    "expected_mm": 10.0, "deviation_pct": 99.0, "reason": ""}
        semantics.register_checker("span", _span_checker)
        try:
            objs = [_object("s", "verified", [_entry("P-S", "span", 20.0, "none")])]
            ir4 = A4Agent().run(_ir2(_param("P-S", 10)), _ir3(objs))
            sem = _sem_issues(ir4)
            assert sem[0]["constraint_id"] == "CHK-SEM-SPAN"
            assert sem[0]["severity"] == "error"
        finally:
            semantics.SEMANTIC_CHECKERS.pop("span", None)

    def test_dim_tolerance_override(self):
        semantics.DIM_TOL_PCT["length"] = 0.0  # 0 容差：0.5% 偏差即 violation
        try:
            objs = [_object("r", "verified", [_entry("P-L", "length", 100.5, "bbox_axis")])]
            ir4 = A4Agent().run(_ir2(_param("P-L", 100)), _ir3(objs))
            assert _sem_issues(ir4)[0]["severity"] == "error"
        finally:
            semantics.DIM_TOL_PCT["length"] = None


# ------------------------------------------------------------------------------
# coverage 与 legacy 排除交互
class TestCoverageAndLegacyInterplay:
    def test_coverage_counts_declared_params(self):
        objs = [
            _object("a", "verified", [_entry("P-1", "length", 100.0, "bbox_axis")]),
            _object("b", "verified", [_entry("P-2", "diameter", 27.0, "radius_derived")], fid="F-002"),
        ]
        ir4 = A4Agent().run(_ir2(_param("P-1", 100), _param("P-2", 27)), _ir3(objs))
        assert ir4["coverage"]["applicable"] == 2
        assert ir4["coverage"]["checked"] == 2
        assert ir4["coverage"]["ratio"] == 100.0

    def test_legacy_numeric_excluded_for_covered_params(self):
        """P-1 既被 verified 映射覆盖又有 legacy 联动 → 只出 CHK-SEM-*，不出 CHK-GEO-NUM
        （不双重裁决），coverage 不重复计数。"""
        objs = [_object("a", "verified", [_entry("P-1", "length", 100.0, "bbox_axis")])]
        ir3 = _ir3(objs, features=[_feature("F-001", linked="P-1", dim=2)], extracted=True)
        ir4 = A4Agent().run(_ir2(_param("P-1", 100)), ir3)
        codes = [i["constraint_id"] for i in ir4["issues"]]
        assert "CHK-GEO-NUM" not in codes
        assert "CHK-SEM-LENGTH" in codes
        assert ir4["coverage"]["applicable"] == 1  # 只计语义链路一次

    def test_uncovered_link_still_checked_by_legacy(self):
        """未被映射覆盖的联动参数仍走 legacy CHK-GEO-NUM。"""
        objs = [_object("a", "verified", [_entry("P-1", "length", 100.0, "bbox_axis")])]
        ir3 = _ir3(objs, features=[
            _feature("F-001", linked="P-1", dim=2),
            _feature("F-002", linked="P-2", dim=2),
        ], extracted=True)
        ir4 = A4Agent().run(_ir2(_param("P-1", 100), _param("P-2", 100)), ir3)
        codes = [i["constraint_id"] for i in ir4["issues"]]
        assert "CHK-GEO-NUM" in codes and "CHK-SEM-LENGTH" in codes

    def test_covered_all_no_legacy_skip_issue(self):
        """联动存在但全部被映射覆盖 → 不出 CHK-GEO-NUM-SKIP（不是应检未检，是换链路了）。"""
        objs = [_object("a", "verified", [_entry("P-1", "length", 100.0, "bbox_axis")])]
        ir3 = {"cadquery_script": "import math\n", "step_file": STEP_REF,
               "geometry_summary": {"source": "extracted", "bbox_mm": [1, 1, 1],
                                    "volume_mm3": 1.0, "part_count": 1},
               "features": [_feature("F-001", linked="P-1", dim=2)],
               "param_mapping": {"version": "1", "identity_basis": "declared_objects",
                                 "objects": objs}}
        ir4 = A4Agent().run(_ir2(_param("P-1", 100)), ir3)
        codes = [i["constraint_id"] for i in ir4["issues"]]
        assert "CHK-GEO-NUM-SKIP" not in codes

    def test_no_mapping_no_semantic_issues(self):
        ir4 = A4Agent().run(_ir2(_param("P-1", 100)), _ir3(None))
        assert _sem_issues(ir4) == []
        assert ir4["coverage"] is None
