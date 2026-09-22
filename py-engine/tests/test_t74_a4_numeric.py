"""T7.4 A4 数值级几何复核单测（PHASE-007）。

覆盖 PHASE-007 §2.5 全部分支：
1. linked 参数 ≤2% → CHK-GEO-NUM info（overall pass）；
2. 超容差（>2%，mockup 06 口径）→ CHK-GEO-NUM error（overall fail，suggestion=back_to_A3）；
3. extracted 但全部未联动 → CHK-GEO-NUM-SKIP info；
4. extraction_failed → CHK-GEO-NUM-SKIP info；
5. 旧版本快照（features 无实测数值）→ CHK-GEO-NUM-SKIP info；
6. 退化几何（part_count=0 / 体积≤0）→ CHK-GEO-DEGENERATE error。
另有：引用完整性检查不被数值支路破坏；A4 仍无 LLM、确定性（同输入同输出）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent

PARAM = {"id": "P-001", "name": "湿端段直径", "value": 54, "unit": "mm",
         "source_type": "case", "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


def _feature(linked, bbox=None, dim=None, fid="F-001"):
    geometry = {"bbox_mm": bbox, "volume_mm3": 1099320.0, "center_mm": [0, 0, 240]} if bbox else {}
    if dim is not None:
        geometry["linked_dim"] = dim
    return {"feature_id": fid, "segment": "SEG-01", "geometry": geometry, "linked_param_id": linked}


def _ir3(features, summary=None):
    ir3 = {
        "cadquery_script": "import cadquery as cq",
        "step_file": {"bucket": "step-models", "object_key": "s1/m.step"},
        "features": features,
    }
    if summary is not None:
        ir3["geometry_summary"] = summary
    return ir3


EXTRACTED = {"source": "extracted", "bbox_mm": [54.0, 54.0, 480.0],
             "volume_mm3": 1099320.0, "part_count": 1}


def _codes(issues):
    return [(i["constraint_id"], i["severity"]) for i in issues]


# ------------------------------------------------------------------------------
def test_within_tolerance_is_info_and_pass():
    """分支 1：54mm 参数 vs 实测 54.5mm（偏差 0.93% ≤ 2%）→ info，overall pass。"""
    ir3 = _ir3([_feature("P-001", bbox=[54.5, 54.5, 480.0], dim=0)], EXTRACTED)
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-NUM", "info") in _codes(ir4["issues"])
    assert ir4["overall"] == "pass"
    msg = next(i["message"] for i in ir4["issues"] if i["constraint_id"] == "CHK-GEO-NUM")
    assert "54" in msg and "0.93%" in msg


def test_over_tolerance_is_error_and_fail():
    """分支 2：54mm 参数 vs 实测 60mm（偏差 11.1% > 2%）→ error，overall fail。"""
    ir3 = _ir3([_feature("P-001", bbox=[60.0, 60.0, 480.0], dim=0)], EXTRACTED)
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    num = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-GEO-NUM"]
    assert len(num) == 1 and num[0]["severity"] == "error"
    assert ir4["overall"] == "fail"
    assert num[0]["suggestion"] == "back_to_A3"
    assert "11.1%" in num[0]["message"] and "容差" in num[0]["message"]


def test_extracted_all_unlinked_is_skip():
    """分支 3：实测提取但全部 null 联动 → 如实 skip，不阻断。"""
    ir3 = _ir3([_feature(None, bbox=[54.0, 54.0, 480.0])], EXTRACTED)
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-NUM-SKIP", "info") in _codes(ir4["issues"])
    assert ir4["overall"] == "pass"
    assert not any(c == "CHK-GEO-NUM" for c, _ in _codes(ir4["issues"]))


def test_extraction_failed_is_skip():
    """分支 4：提取失败 → 如实 skip。"""
    ir3 = _ir3([], {"source": "extraction_failed", "detail": "subprocess timeout after 60s"})
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    skips = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-GEO-NUM-SKIP"]
    assert len(skips) == 1 and skips[0]["severity"] == "info"
    assert "timeout" in skips[0]["message"]
    assert ir4["overall"] == "pass"


def test_legacy_snapshot_is_skip():
    """分支 5：旧快照（占位镜像 geometry 无 bbox）→ 如实 skip，引用检查照常。"""
    legacy_feature = {"feature_id": "F-001", "segment": "model",
                      "geometry": {"linked_param": "P-001", "note": "占位"},
                      "linked_param_id": "P-001"}
    ir3 = _ir3([legacy_feature])
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-NUM-SKIP", "info") in _codes(ir4["issues"])
    assert ir4["overall"] == "pass"


def test_degenerate_geometry_is_error():
    """分支 6a：part_count=0 → DEGENERATE error。"""
    ir3 = _ir3([], {"source": "extracted", "bbox_mm": [0.0, 0.0, 0.0],
                    "volume_mm3": 0.0, "part_count": 0})
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-DEGENERATE", "error") in _codes(ir4["issues"])
    assert ir4["overall"] == "fail"

    # 分支 6b：体积 ≤0 同样 error
    ir3 = _ir3([], {"source": "extracted", "bbox_mm": [1.0, 1.0, 1.0],
                    "volume_mm3": -0.5, "part_count": 1})
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-DEGENERATE", "error") in _codes(ir4["issues"])


def test_unit_m_converted_and_non_length_skipped():
    """单位换算：23.5m=23500mm vs 实测 23500 → 一致；非长度单位参数联动 → 逐参 skip。"""
    param_m = dict(PARAM, id="P-002", name="阵列孔径", value=23.5, unit="m")
    ir3 = _ir3([_feature("P-002", bbox=[54.0, 54.0, 23500.0], dim=2)],
               EXTRACTED | {"part_count": 1, "bbox_mm": [54.0, 54.0, 23500.0], "volume_mm3": 1.0})
    ir4 = A4Agent().run({"parameters": [param_m]}, ir3)
    assert ir4["overall"] == "pass"
    assert any(c == "CHK-GEO-NUM" and s == "info" for c, s in _codes(ir4["issues"]))

    param_khz = dict(PARAM, id="P-003", name="频率", value=12, unit="kHz")
    ir3 = _ir3([_feature("P-003", bbox=[54.0, 54.0, 480.0], dim=0)], EXTRACTED)
    ir4 = A4Agent().run({"parameters": [param_khz]}, ir3)
    assert ("CHK-GEO-NUM-SKIP", "info") in _codes(ir4["issues"])


def test_reference_checks_survive_and_deterministic():
    """数值支路不破坏既有引用完整性检查；同输入同输出（确定性）。"""
    bad_feature = _feature("P-404", bbox=[54.0, 54.0, 480.0], dim=0)  # 悬空引用
    ir3 = _ir3([bad_feature], EXTRACTED)
    agent = A4Agent()
    r1 = agent.run({"parameters": [PARAM]}, ir3)
    assert ("CHK-GEO-LINK", "error") in _codes(r1["issues"])
    r2 = agent.run({"parameters": [PARAM]}, ir3)
    assert r1 == r2
