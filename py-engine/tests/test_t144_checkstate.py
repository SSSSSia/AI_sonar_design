# -*- coding: utf-8 -*-
"""T14.4 单测：校验状态学契约与覆盖率口径（外部审查问题 2）。

- 用例 14：应检未检（unverified）驱动 overall=incomplete，不表现为完整通过；
- 优先级：error > unverified > 无（fail > incomplete > pass）；
- 向后兼容：旧两态快照（无 coverage）合法；incomplete/unverified 新枚举合法。
"""

import sys
from pathlib import Path

import pytest
from jsonschema import validate as _v

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent
from ir_contract import load_schema

PARAM = {"id": "P-001", "name": "湿端段直径", "value": 54, "unit": "mm",
         "source_type": "case", "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"}


def _ir3(features, summary=None):
    ir3 = {
        "cadquery_script": "import cadquery as cq",
        "step_file": {"bucket": "step-models", "object_key": "s1/m.step"},
        "features": features,
    }
    if summary is not None:
        ir3["geometry_summary"] = summary
    return ir3


def _feature(linked, bbox, dim=0, fid="F-001"):
    return {"feature_id": fid, "segment": "SEG-01",
            "geometry": {"bbox_mm": bbox, "volume_mm3": 1.0, "center_mm": [0, 0, 0],
                         "linked_dim": dim},
            "linked_param_id": linked}


EXTRACTED = {"source": "extracted", "bbox_mm": [54.0, 54.0, 480.0],
             "volume_mm3": 1.0, "part_count": 1}


def test_case14_unverified_never_pass():
    """审查验收 14：缺公式定位（unverified）时不得显示完整通过。"""
    params = [dict(PARAM, source_type="formula", source_ref="自定义手册#某公式",
                   derivation="x=1")]
    ir4 = A4Agent().run({"parameters": params}, _ir3([], EXTRACTED))
    # formula 无法定位 → unverified；几何 extracted 但无联动 → unverified
    unverified = [i for i in ir4["issues"] if i["severity"] == "unverified"]
    assert len(unverified) >= 2
    assert ir4["overall"] == "incomplete"
    assert ir4["coverage"] == {"checked": 0, "applicable": 1, "ratio": 0.0}


def test_error_beats_incomplete():
    """优先级：存在 error（超差）→ fail，即使同时有 unverified。"""
    ir3 = _ir3([_feature("P-001", [70.0, 70.0, 480.0])], EXTRACTED)  # 54 vs 70 超差
    ir4 = A4Agent().run({"parameters": [PARAM]}, ir3)
    assert ir4["overall"] == "fail"


def test_old_two_state_snapshot_still_valid():
    """向后兼容（D14-2/纪律）：旧快照两态 overall、无 coverage 字段 → schema 合法。"""
    schema = load_schema("IR4")
    _v({"overall": "pass", "issues": [], "csr": None}, schema)
    _v({"overall": "fail", "issues": [], "csr": 88.88}, schema)


def test_new_fields_valid_against_schema():
    schema = load_schema("IR4")
    _v({
        "overall": "incomplete",
        "issues": [{"constraint_id": "CHK-GEO-NUM-SKIP", "severity": "unverified",
                    "message": "几何复核未执行", "related_params": [], "related_features": []}],
        "csr": None,
        "coverage": {"checked": 1, "applicable": 4, "ratio": 25.0},
    }, schema)
    with pytest.raises(Exception):
        _v({"overall": "partial", "issues": []}, schema)  # 枚举外仍拒绝
    with pytest.raises(Exception):
        _v({"overall": "pass", "issues": [], "coverage": {"checked": -1, "applicable": 0}}, schema)


def test_coverage_none_when_no_applicable():
    """applicable=0（无 formula/无适用军标 expr/无联动几何）→ coverage=None（A-16）。"""
    ir4 = A4Agent().run({"parameters": [dict(PARAM, source_type="estimated")]},
                        _ir3([], {"source": "extraction_failed", "detail": "x"}))
    assert ir4["coverage"] is None
    assert ir4["overall"] == "incomplete"
