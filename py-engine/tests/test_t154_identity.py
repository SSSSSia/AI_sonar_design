# -*- coding: utf-8 -*-
"""T15.4 身份匹配扩展单测——WP1 legacy 几何候选身份（PHASE-015 §3.8，ADR-014 §6）。

- build_candidate_mapping：四态落点（唯一最优→verified geometry_candidate /
  并列→ambiguous / 被占用→unmatched / 无候选→不进映射）、同段 diameter 优先、
  案例 03 口径（无圆度证据不造孔径）、旋转不变、单位换算；
- A3Agent._build_ir3 集成：无声明→geometry_candidates 映射 / A3_LEGACY_MAPPING=0 回退
  T15.2 口径 / 声明制脚本不受影响；
- A4 文案按 identity_basis 区分「声明/几何候选」（A-16 不修饰）+ covered_param_ids。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a3_cad.agent import A3Agent
from agents.a3_cad.identity import build_candidate_mapping
from agents.a4_validation.agent import A4Agent
from agents.a4_validation.semantics import covered_param_ids
from ir_contract import validate_ir
from tests.test_t73_a3_geometry import SCRIPT_REF, STEP_REF  # noqa: F401


def _part(idx, bbox, radius=None, axis=2, z=0.0, volume=100.0):
    p = {"index": idx, "center_mm": [0.0, 0.0, z], "bbox_mm": list(bbox), "volume_mm3": volume}
    p["axis"] = axis
    if radius is not None:
        p["radius_mm"] = radius
    return p


def _param(pid, value, unit="mm"):
    return {"id": pid, "name": pid, "value": value, "unit": unit, "source_type": "case",
            "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


# Ø54×480 圆柱段（轴向 z）与 Ø40×1200 段——与 test_t152 夹具同源
_CYL_480 = _part(1, [54.0, 54.0, 480.0], radius=27.0, axis=2, z=240.0)
_CYL_1200 = _part(2, [40.0, 40.0, 1200.0], radius=20.0, axis=2, z=1200.0)


# ------------------------------------------------------------------------------
# build_candidate_mapping 四态（WP1）
class TestLegacyCandidateMapping:
    def test_unique_candidate_verified(self):
        m = build_candidate_mapping([_param("P-LEN", 480)], [_CYL_480, _CYL_1200], 20.0)
        assert m["version"] == "1" and m["identity_basis"] == "geometry_candidates"
        (obj,) = m["objects"]
        assert obj["object_id"] == "P-LEN" and obj["role"] == "legacy_param"
        assert obj["status"] == "verified" and obj["basis"] == "geometry_candidate"
        assert obj["feature_id"] == "F-001"
        assert obj["params"] == [{"param_id": "P-LEN", "dim_type": "length",
                                  "measured_mm": 480.0, "measurement": "bbox_axis"}]

    def test_same_size_tie_ambiguous(self):
        """同尺寸反例：两段同尺寸不可区分个体 → ambiguous（歧义必须可见，不假装挑选）。"""
        twin = _part(2, [54.0, 54.0, 480.0], radius=27.0, axis=2, z=960.0)
        m = build_candidate_mapping([_param("P-LEN", 480)], [_CYL_480, twin], 20.0)
        (obj,) = m["objects"]
        assert obj["status"] == "ambiguous"
        assert "basis" not in obj and obj["feature_id"] is None
        assert obj["params"][0]["measured_mm"] is None
        assert obj["params"][0]["measurement"] == "none"

    def test_candidate_claimed_by_better_param_unmatched(self):
        """候选被更优参数占用 → unmatched（有候选但身份让位，如实；link_params 旧口径静默丢）。"""
        m = build_candidate_mapping(
            [_param("P-A", 480), _param("P-B", 490)], [_CYL_480, _CYL_1200], 20.0)
        by_id = {o["object_id"]: o for o in m["objects"]}
        assert by_id["P-A"]["status"] == "verified" and by_id["P-A"]["feature_id"] == "F-001"
        assert by_id["P-B"]["status"] == "unmatched"
        assert by_id["P-B"]["feature_id"] is None
        assert by_id["P-B"]["params"][0]["measured_mm"] is None

    def test_no_candidate_excluded(self):
        """无任何容差内候选 → 不进映射（legacy CHK-GEO-NUM/SKIP 照旧裁决）。"""
        assert build_candidate_mapping([_param("P-X", 9999)], [_CYL_480, _CYL_1200], 20.0) is None
        m = build_candidate_mapping(
            [_param("P-LEN", 480), _param("P-X", 9999)], [_CYL_480, _CYL_1200], 20.0)
        assert [o["object_id"] for o in m["objects"]] == ["P-LEN"]

    def test_diameter_roundness_preferred(self):
        """同段同中 diameter（radius_derived）与 bbox 横维数值重合 → 取圆度证据。"""
        m = build_candidate_mapping([_param("P-D", 54)], [_CYL_480, _CYL_1200], 20.0)
        (obj,) = m["objects"]
        assert obj["status"] == "verified"
        assert obj["params"][0]["dim_type"] == "diameter"
        assert obj["params"][0]["measurement"] == "radius_derived"
        assert obj["params"][0]["measured_mm"] == 54.0

    def test_case03_box_no_roundness_no_diameter(self):
        """案例 03 口径：方形截面（无 radius_mm）不造孔径——同值不进映射（横维非长度证据）。"""
        box = _part(1, [54.0, 54.0, 480.0], axis=2, z=240.0)
        m = build_candidate_mapping([_param("P-W", 54), _param("P-LEN", 480)], [box], 20.0)
        assert [o["object_id"] for o in m["objects"]] == ["P-LEN"]
        assert m["objects"][0]["params"][0]["dim_type"] == "length"

    def test_rotation_invariance(self):
        """旋转反例：同一圆柱沿 X 与沿 Z，绑定/测量不依赖世界轴（axis 随提取器旋转不变）。"""
        along_z = build_candidate_mapping([_param("P-LEN", 480)], [_CYL_480], 20.0)
        cyl_x = _part(1, [480.0, 54.0, 54.0], radius=27.0, axis=0)
        along_x = build_candidate_mapping([_param("P-LEN", 480)], [cyl_x], 20.0)
        oz, ox = along_z["objects"][0], along_x["objects"][0]
        assert ox["status"] == "verified" and oz["status"] == "verified"
        assert ox["params"][0]["measured_mm"] == oz["params"][0]["measured_mm"] == 480.0
        assert ox["params"][0]["measurement"] == oz["params"][0]["measurement"] == "bbox_axis"

    def test_unit_conversion(self):
        m = build_candidate_mapping([_param("P-LEN", 0.48, unit="m")], [_CYL_480], 20.0)
        assert m["objects"][0]["status"] == "verified"
        assert m["objects"][0]["params"][0]["measured_mm"] == 480.0

    def test_closed_or_empty_inputs(self):
        assert build_candidate_mapping([_param("P", 480)], [_CYL_480], 0.0) is None
        assert build_candidate_mapping([], [_CYL_480], 20.0) is None
        assert build_candidate_mapping([_param("P", 480)], [], 20.0) is None


# ------------------------------------------------------------------------------
# _build_ir3 集成（无声明脚本 + T15.2 同源三段夹具）
INSPECTED_154 = {
    "ok": True,
    "overall": {"bbox_mm": [54.0, 54.0, 29440.0], "volume_mm3": 56430528.0, "part_count": 3},
    "parts": [
        {"index": 1, "center_mm": [0, 0, 240], "bbox_mm": [54.0, 54.0, 480.0],
         "volume_mm3": 1099320.0, "axis": 2, "radius_mm": 27.0},
        {"index": 2, "center_mm": [0, 0, 12230], "bbox_mm": [54.0, 54.0, 23500.0],
         "volume_mm3": 53823244.0, "axis": 2, "radius_mm": 27.0},
        {"index": 3, "center_mm": [0, 0, 28840], "bbox_mm": [40.0, 40.0, 1200.0],
         "volume_mm3": 1507964.0, "axis": 2, "radius_mm": 20.0},
    ],
    "meta": {"extractor": "cadquery-occt/trimesh", "tolerance_mm": 1.0,
             "elapsed_s": 0.4, "segmentation": "radius_profile"},
}

PLAIN_SCRIPT = "import math\nassy.save('x.step')\n"  # 无任何声明（旧 STEP/legacy）


def _agent_154() -> A3Agent:
    agent = A3Agent(llm=None)
    agent._inspect_step = lambda path: INSPECTED_154  # type: ignore[method-assign]
    return agent


class TestLegacyBuildIr3:
    def test_emitted_with_geometry_candidates(self):
        ir3 = validate_ir("IR3", _agent_154()._build_ir3(
            PLAIN_SCRIPT, STEP_REF, SCRIPT_REF,
            {"parameters": [_param("P-LEN1", 480), _param("P-003", 40)]},
            "/tmp/x.step", fix_round=0))
        mapping = ir3["param_mapping"]
        assert mapping["identity_basis"] == "geometry_candidates" and mapping["version"] == "1"
        by_id = {o["object_id"]: o for o in mapping["objects"]}
        assert by_id["P-LEN1"]["status"] == "verified"
        assert by_id["P-LEN1"]["basis"] == "geometry_candidate"
        assert by_id["P-LEN1"]["feature_id"] == "F-001"
        assert by_id["P-003"]["params"][0] == {
            "param_id": "P-003", "dim_type": "diameter",
            "measured_mm": 40.0, "measurement": "radius_derived"}

    def test_env_off_restores_t152_behavior(self, monkeypatch):
        """回退开关：A3_LEGACY_MAPPING=0 → 无声明省略字段（T15.2 口径，ADR-014 §6）。"""
        monkeypatch.setattr("agents.a3_cad.agent.A3_LEGACY_MAPPING", False)
        ir3 = validate_ir("IR3", _agent_154()._build_ir3(
            PLAIN_SCRIPT, STEP_REF, SCRIPT_REF,
            {"parameters": [_param("P-LEN1", 480)]}, "/tmp/x.step", fix_round=0))
        assert "param_mapping" not in ir3

    def test_declared_script_unaffected(self):
        """声明制脚本照走 declared_objects（legacy 候选只在无声明分支）。"""
        script = "# @obj seg1 role=hose_section params=P-LEN1:length\n" + PLAIN_SCRIPT
        ir3 = validate_ir("IR3", _agent_154()._build_ir3(
            script, STEP_REF, SCRIPT_REF,
            {"parameters": [_param("P-LEN1", 480)]}, "/tmp/x.step", fix_round=0))
        assert ir3["param_mapping"]["identity_basis"] == "declared_objects"


# ------------------------------------------------------------------------------
# A4 文案按身份依据区分（A-16 不修饰：候选不是声明）
class TestA4LegacyWording:
    @staticmethod
    def _legacy_mapping_ambiguous() -> dict:
        twin = _part(2, [54.0, 54.0, 480.0], radius=27.0, axis=2, z=960.0)
        return build_candidate_mapping([_param("P-LEN", 480)], [_CYL_480, twin], 20.0)

    def test_mapping_noun_legacy(self):
        msg = A4Agent._check_semantic_mapping(
            {"parameters": [{"id": "P-LEN"}]},
            {"param_mapping": self._legacy_mapping_ambiguous()})[0]["message"]
        assert "几何候选" in msg and "声明" not in msg

    def test_mapping_noun_declared(self):
        ir3 = {"param_mapping": {"identity_basis": "declared_objects", "objects": [
            {"object_id": "seg1", "role": "r", "feature_id": None, "status": "ambiguous",
             "params": [{"param_id": "P-1", "dim_type": "length", "measured_mm": None,
                         "measurement": "none"}]}]}}
        msg = A4Agent._check_semantic_mapping({"parameters": [{"id": "P-1"}]}, ir3)[0]["message"]
        assert "声明" in msg and "几何候选" not in msg

    def test_identity_noun_default_is_declared(self):
        assert A4Agent._identity_noun({}) == "声明"
        assert A4Agent._identity_noun({"param_mapping": {
            "identity_basis": "geometry_candidates"}}) == "几何候选"

    def test_covered_param_ids_includes_legacy(self):
        ir3 = {"param_mapping": self._legacy_mapping_ambiguous()}
        assert covered_param_ids(ir3) == {"P-LEN"}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
