# -*- coding: utf-8 -*-
"""T15.2 映射/身份最小实现单测（PHASE-015，ADR-014 §2）。

- parse_object_declarations：单对象/区间语法/畸形容错/重复/尺寸类型校验；
- build_param_mapping：声明制身份四态（verified/ambiguous/unmatched）、对称类计数核验、
  案例04 交换反例（身份随声明+几何而非位置）、案例02 机制（绑定不以数值相近为门槛）、
  案例03 机制（无圆度证据的段不能顶替孔径绑定）、role-only 消除绑定；
- A3Agent._build_ir3 集成：param_mapping 进 IR3 / 无声明省略 / 提取失败省略；
- A4Agent._check_semantic_mapping：ambiguous/unmatched → CHK-SEM-MAPPING unverified。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a3_cad.agent import A3Agent
from agents.a3_cad.identity import build_param_mapping, parse_object_declarations
from agents.a4_validation.agent import A4Agent
from ir_contract import validate_ir
from tests.test_t73_a3_geometry import INSPECTED, SCRIPT_REF, STEP_REF  # noqa: F401


def _part(idx, bbox, radius=None, axis=2, z=0.0):
    p = {"index": idx, "center_mm": [0.0, 0.0, z], "bbox_mm": list(bbox), "volume_mm3": 100.0}
    p["axis"] = axis
    if radius is not None:
        p["radius_mm"] = radius
    return p


def _param(pid, value, unit="mm"):
    return {"id": pid, "name": pid, "value": value, "unit": unit, "source_type": "case",
            "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


# ------------------------------------------------------------------------------
# 解析器
class TestParseDeclarations:
    def test_single_and_params(self):
        decls = parse_object_declarations(
            "# @obj winch role=winch params=p_len:length,p_dia:diameter\n")
        assert decls == [{"object_id": "winch", "role": "winch",
                          "params": [{"param_id": "p_len", "dim_type": "length"},
                                     {"param_id": "p_dia", "dim_type": "diameter"}]}]

    def test_range_expansion_zero_pad(self):
        decls = parse_object_declarations("# @obj elem-[01-03] role=array_element\n")
        assert [d["object_id"] for d in decls] == ["elem-01", "elem-02", "elem-03"]
        assert all(d["role"] == "array_element" and d["params"] == [] for d in decls)

    def test_malformed_skipped(self):
        # 缺 role / 区间非法 / 参数段非法（dim 未知、缺冒号）→ 对应部分跳过
        decls = parse_object_declarations(
            "# @obj a\n"                                  # 缺 role → 整行跳过
            "# @obj b-[05-01] role=x\n"                   # 逆序区间 → 跳过
            "# @obj c role=y params=p1:diameter,p2:volume,p3length\n"  # p2/p3 段非法
            "# @obj d role=z params=p4:width\n"
        )
        assert [d["object_id"] for d in decls] == ["c", "d"]
        assert decls[0]["params"] == [{"param_id": "p1", "dim_type": "diameter"}]

    def test_duplicate_id_keeps_first(self):
        decls = parse_object_declarations(
            "# @obj a role=x params=p1:length\n# @obj a role=y params=p2:length\n")
        assert len(decls) == 1 and decls[0]["role"] == "x"


# ------------------------------------------------------------------------------
# 匹配器四态与反例
class TestBuildParamMapping:
    def test_singleton_verified_declared_tag(self):
        parts = [_part(1, [20.0, 20.0, 2000.0], radius=10.0)]
        decls = [{"object_id": "lead", "role": "lead_cable",
                  "params": [{"param_id": "p_l", "dim_type": "length"},
                             {"param_id": "p_d", "dim_type": "diameter"}]}]
        m = build_param_mapping(decls, parts, [_param("p_l", 2000), _param("p_d", 20)], 20.0)
        o = m["objects"][0]
        assert o["status"] == "verified" and o["basis"] == "declared_tag"
        assert o["feature_id"] == "F-001"
        assert {p["dim_type"]: (p["measured_mm"], p["measurement"]) for p in o["params"]} == {
            "length": (2000.0, "bbox_axis"), "diameter": (20.0, "radius_derived")}
        assert m["version"] == "1" and m["identity_basis"] == "declared_objects"

    def test_case04_swap_identity_follows_geometry_not_position(self):
        """案例 04：两段换位后身份不互换——绑定随声明+几何证据，不随位置索引。"""
        decls = [
            {"object_id": "lead", "role": "lead_cable",
             "params": [{"param_id": "p_lead", "dim_type": "length"}]},
            {"object_id": "array", "role": "array_section",
             "params": [{"param_id": "p_arr", "dim_type": "length"}]},
        ]
        params = [_param("p_lead", 2000), _param("p_arr", 24000)]
        normal = [_part(1, [20.0, 20.0, 2000.0], z=0), _part(2, [27.0, 27.0, 24000.0], z=10000)]
        swapped = [_part(1, [27.0, 27.0, 24000.0], z=0), _part(2, [20.0, 20.0, 2000.0], z=20000)]
        m1 = build_param_mapping(decls, normal, params, 20.0)
        m2 = build_param_mapping(decls, swapped, params, 20.0)
        b1 = {o["object_id"]: o["feature_id"] for o in m1["objects"]}
        b2 = {o["object_id"]: o["feature_id"] for o in m2["objects"]}
        assert b1 == {"lead": "F-001", "array": "F-002"}
        assert b2 == {"lead": "F-002", "array": "F-001"}  # 身份随几何，不随 index
        assert all(o["status"] == "verified" for o in m2["objects"])

    def test_symmetric_class_verified_and_count_mismatch(self):
        """对称类：16 声明 ↔ 3 实测 → 3 verified + 13 unmatched（数量不齐如实）。"""
        decls = [{"object_id": f"elem-{i:02d}", "role": "array_element",
                  "params": [{"param_id": "p_d", "dim_type": "diameter"}]} for i in range(1, 17)]
        parts = [_part(i, [12.0, 12.0, 60.0], radius=6.0, z=i * 100.0) for i in range(1, 4)]
        m = build_param_mapping(decls, parts, [_param("p_d", 12)], 20.0)
        statuses = [o["status"] for o in m["objects"]]
        assert statuses.count("verified") == 3 and statuses.count("unmatched") == 13
        v = m["objects"][0]
        assert v["basis"] == "symmetric_class" and v["symmetric_size"] == 16
        assert v["params"][0]["measured_mm"] == 12.0

    def test_tie_is_ambiguous(self):
        """两段几何无法区分（同尺寸）而声明参数不同 → 评分并列 → ambiguous。"""
        decls = [
            {"object_id": "a", "role": "block",
             "params": [{"param_id": "p_a", "dim_type": "length"}]},
            {"object_id": "b", "role": "block",
             "params": [{"param_id": "p_b", "dim_type": "length"}]},
        ]
        parts = [_part(1, [10.0, 10.0, 500.0], z=0), _part(2, [10.0, 10.0, 500.0], z=600)]
        m = build_param_mapping(decls, parts, [_param("p_a", 500), _param("p_b", 900)], 20.0)
        assert all(o["status"] == "ambiguous" and o["feature_id"] is None for o in m["objects"])

    def test_unmatched_when_no_evidence(self):
        """孔径声明但全部段无圆度证据 → unmatched（不造直径测量，A-16）。"""
        decls = [{"object_id": "cyl", "role": "array_element",
                  "params": [{"param_id": "p_d", "dim_type": "diameter"}]}]
        parts = [_part(1, [30.0, 30.0, 200.0])]  # 无 radius_mm（bbox 方形截面）
        m = build_param_mapping(decls, parts, [_param("p_d", 30)], 20.0)
        assert m["objects"][0]["status"] == "unmatched"
        assert m["objects"][0]["params"][0]["measurement"] == "none"

    def test_case03_no_diameter_evidence_cannot_steal_binding(self):
        """案例 03 机制：同值宽度的方形截面段（无圆度证据）不能顶替孔径绑定——
        孔径只认 radius_derived；圆柱偏差 20% 也照绑，由检查器报超差。"""
        decls = [
            {"object_id": "elem", "role": "array_element",
             "params": [{"param_id": "p_aper", "dim_type": "diameter"}]},
            {"object_id": "box", "role": "cabinet",
             "params": [{"param_id": "p_box_l", "dim_type": "length"}]},
        ]
        params = [_param("p_aper", 100), _param("p_box_l", 500)]
        parts = [
            _part(1, [100.0, 100.0, 500.0], z=0),          # 方形截面宽 100（无 radius_mm）
            _part(2, [80.0, 80.0, 300.0], radius=40.0, z=600),  # 圆柱 Ø80（孔径超差件）
        ]
        m = build_param_mapping(decls, parts, params, 20.0)
        by_id = {o["object_id"]: o for o in m["objects"]}
        assert by_id["elem"]["feature_id"] == "F-002"  # 孔径绑定圆柱，不被同值宽度顶替
        assert by_id["elem"]["params"][0]["measured_mm"] == 80.0
        assert by_id["elem"]["params"][0]["measurement"] == "radius_derived"
        assert by_id["box"]["feature_id"] == "F-001"

    def test_case02_binding_not_gated_by_value_proximity(self):
        """案例 02 机制：100 要求 vs 150 实测（50% > 联动容差 20%）仍建立映射并记录
        实测——超差判定交给语义检查器（必报 error），而非 unverified 漏检。"""
        decls = [{"object_id": "rod", "role": "rod",
                  "params": [{"param_id": "p_l", "dim_type": "length"}]}]
        parts = [_part(1, [10.0, 10.0, 150.0])]
        m = build_param_mapping(decls, parts, [_param("p_l", 100)], 20.0)
        o = m["objects"][0]
        assert o["status"] == "verified" and o["params"][0]["measured_mm"] == 150.0

    def test_role_only_elimination(self):
        """role-only：唯一剩余段 → geometry_unique；剩余段多余 → ambiguous。"""
        decls = [{"object_id": "x", "role": "bracket", "params": []}]
        one = [_part(1, [10.0, 10.0, 10.0])]
        m1 = build_param_mapping(decls, one, [], 20.0)
        assert m1["objects"][0]["status"] == "verified"
        assert m1["objects"][0]["basis"] == "geometry_unique"
        two = [_part(1, [10.0, 10.0, 10.0]), _part(2, [20.0, 20.0, 20.0])]
        m2 = build_param_mapping(decls, two, [], 20.0)
        assert m2["objects"][0]["status"] == "ambiguous"

    def test_unit_conversion_m_cm(self):
        decls = [{"object_id": "s", "role": "rod",
                  "params": [{"param_id": "p_l", "dim_type": "length"}]}]
        parts = [_part(1, [10.0, 10.0, 150.0])]
        m = build_param_mapping(decls, parts, [_param("p_l", 0.15, "m")], 20.0)
        assert m["objects"][0]["params"][0]["measured_mm"] == 150.0

    def test_empty_inputs_return_none(self):
        assert build_param_mapping([], [_part(1, [1, 1, 1])], [], 20.0) is None
        assert build_param_mapping([{"object_id": "a", "role": "r", "params": []}], [], [], 20.0) is None


# ------------------------------------------------------------------------------
# _build_ir3 集成（本地夹具：三段 Ø54×480 / Ø54×23500 / Ø40×1200，附 T15.2 圆度证据）
INSPECTED_152 = {
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

DECLARED_SCRIPT = (
    "import math\n"
    "# @obj seg1 role=hose_section params=P-LEN1:length,P-001:diameter\n"
    "# @obj seg3 role=hose_section params=P-LEN3:length,P-003:diameter\n"
    "assy.save('x.step')\n"
)


def _agent_152() -> A3Agent:
    agent = A3Agent(llm=None)
    agent._inspect_step = lambda path: INSPECTED_152  # type: ignore[method-assign]
    return agent


class TestBuildIr3Mapping:
    def test_param_mapping_emitted(self):
        ir3 = validate_ir("IR3", _agent_152()._build_ir3(
            DECLARED_SCRIPT, STEP_REF, SCRIPT_REF,
            {"parameters": [
                {"id": "P-001", "name": "直径", "value": 54, "unit": "mm", "source_type": "case",
                 "source_ref": "K", "derivation": "d", "trace_id": "t"},
                {"id": "P-003", "name": "直径3", "value": 40, "unit": "mm", "source_type": "case",
                 "source_ref": "K", "derivation": "d", "trace_id": "t"},
                {"id": "P-LEN1", "name": "长1", "value": 480, "unit": "mm", "source_type": "case",
                 "source_ref": "K", "derivation": "d", "trace_id": "t"},
                {"id": "P-LEN3", "name": "长3", "value": 1200, "unit": "mm", "source_type": "case",
                 "source_ref": "K", "derivation": "d", "trace_id": "t"},
            ]}, "/tmp/x.step", fix_round=0))
        mapping = ir3["param_mapping"]
        assert mapping["version"] == "1" and mapping["identity_basis"] == "declared_objects"
        by_id = {o["object_id"]: o for o in mapping["objects"]}
        assert by_id["seg1"]["status"] == "verified" and by_id["seg1"]["basis"] == "declared_tag"
        assert by_id["seg1"]["feature_id"] == "F-001"
        meas = {p["dim_type"]: (p["measured_mm"], p["measurement"]) for p in by_id["seg1"]["params"]}
        assert meas == {"length": (480.0, "bbox_axis"), "diameter": (54.0, "radius_derived")}
        assert by_id["seg3"]["feature_id"] == "F-003"
        meas3 = {p["dim_type"]: (p["measured_mm"], p["measurement"]) for p in by_id["seg3"]["params"]}
        assert meas3 == {"length": (1200.0, "bbox_axis"), "diameter": (40.0, "radius_derived")}

    def test_no_declarations_field_omitted(self):
        """无声明且无长度类参数 → 映射省略（T15.4 起：有参数时无声明走 legacy 几何候选
        映射 identity_basis=geometry_candidates，见 test_t154_identity；此处空参数仍省略）。"""
        ir3 = validate_ir("IR3", _agent_152()._build_ir3(
            "import math\n", STEP_REF, SCRIPT_REF, {"parameters": []}, "/tmp/x.step", fix_round=0))
        assert "param_mapping" not in ir3

    def test_extraction_failed_mapping_omitted(self):
        """提取失败→无互证素材→映射省略（A-16：无测量不造映射）。"""
        agent = A3Agent(llm=None)
        agent._inspect_step = lambda path: {"ok": False, "error": "boom"}  # type: ignore
        ir3 = validate_ir("IR3", agent._build_ir3(
            DECLARED_SCRIPT, STEP_REF, SCRIPT_REF, {"parameters": []}, "/tmp/x.step", fix_round=0))
        assert "param_mapping" not in ir3


# ------------------------------------------------------------------------------
# A4 CHK-SEM-MAPPING
class TestA4SemanticMapping:
    def test_ambiguous_mapping_unverified(self):
        ir3 = {"param_mapping": {"version": "1", "identity_basis": "declared_objects", "objects": [
            {"object_id": "a", "role": "block", "feature_id": None, "status": "ambiguous",
             "params": [{"param_id": "P-1", "dim_type": "length", "measured_mm": None,
                         "measurement": "none"}]}]}}
        issues = [i for i in A4Agent().run(
            {"design_document": "d", "parameters": [{"id": "P-1"}]}, ir3)["issues"]
            if i["constraint_id"] == "CHK-SEM-MAPPING"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "unverified"
        assert issues[0]["related_params"] == ["P-1"]

    def test_verified_and_role_only_no_issue(self):
        ir3 = {"param_mapping": {"version": "1", "identity_basis": "declared_objects", "objects": [
            {"object_id": "v", "role": "rod", "feature_id": "F-001", "status": "verified",
             "basis": "declared_tag",
             "params": [{"param_id": "P-1", "dim_type": "length", "measured_mm": 100.0,
                         "measurement": "bbox_axis"}]},
            {"object_id": "r", "role": "bracket", "feature_id": None, "status": "ambiguous",
             "params": []}]}}
        issues = [i for i in A4Agent().run(
            {"design_document": "d", "parameters": [{"id": "P-1"}]}, ir3)["issues"]
            if i["constraint_id"] == "CHK-SEM-MAPPING"]
        assert issues == []

    def test_unverified_drives_overall_incomplete(self):
        ir3 = {"cadquery_script": "import math\n",
               "step_file": {"bucket": "step-models", "object_key": "s/x.step"},
               "param_mapping": {"version": "1", "identity_basis": "declared_objects", "objects": [
                   {"object_id": "u", "role": "rod", "feature_id": None, "status": "unmatched",
                    "params": [{"param_id": "P-1", "dim_type": "length", "measured_mm": None,
                                "measurement": "none"}]}]}}
        ir4 = A4Agent().run({"design_document": "d", "parameters": [{"id": "P-1"}]}, ir3)
        assert ir4["overall"] == "incomplete"

    def test_no_mapping_no_issue(self):
        issues = [i for i in A4Agent().run(
            {"design_document": "d", "parameters": []}, {"features": []})["issues"]
            if i["constraint_id"] == "CHK-SEM-MAPPING"]
        assert issues == []
