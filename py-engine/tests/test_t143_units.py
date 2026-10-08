# -*- coding: utf-8 -*-
"""T14.3 单测：单位统一模块（外部审查问题 1）。

回归验收案例 01：20 mm / 2 cm / 0.02 m 必须产生相同归一值与公式结果；
未知单位/范围表达必须显式拒绝，不取首数继续算。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solver import formulas, intervals, units
from solver.units import UnresolvableQuantity, parse_quantity


# ----------------------------------------------------------------------
# 用例 01：等价写法同归一值
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text", ["20 mm", "2 cm", "0.02 m", "0.02米", "20 毫米"])
def test_case01_equivalent_length_writings(text):
    q = parse_quantity(text)
    assert units.convert(q.value, q.unit, "m") == pytest.approx(0.02)


def test_case01_available_inputs_unit_conversion():
    for text in ("20 mm", "2 cm", "0.02 m"):
        inputs = formulas.available_inputs({"阵元间距": text})
        assert inputs["d"] == pytest.approx(0.02), text


def test_case01_formula_result_identical():
    """审查原始探针：同间距不同写法，f_beam 结果不得再差 1000 倍。"""
    results = []
    for text in ("20 mm", "2 cm", "0.02 m"):
        entries = {e["formula_key"]: e for e in formulas.evaluate_available({"阵元间距": text})}
        results.append(entries["beamforming_freq_max"]["value"])
    assert results[0] == results[1] == results[2] == pytest.approx(37500.0)  # c=1500, d=0.02


def test_naked_number_legacy_contract():
    """裸数值按符号登记单位直取（v0.6 前契约兼容）：16 → n=16。"""
    assert formulas.available_inputs({"阵元数": 16})["n"] == 16.0
    assert formulas.available_inputs({"阵元数": "16 个"})["n"] == 16.0  # 中文计数词
    assert formulas.available_inputs({"声速": "1500 m/s"})["c"] == 1500.0


# ----------------------------------------------------------------------
# 拒绝路径（宁缺勿错）
# ----------------------------------------------------------------------
def test_range_expression_rejected():
    assert "d" not in formulas.available_inputs({"阵元间距": "20-30 米"})


def test_unknown_unit_rejected():
    assert "r" not in formulas.available_inputs({"距离": "5 巴"})   # 压力族未登记
    assert "r" not in formulas.available_inputs({"距离": "30 °"})   # 角度族未登记


def test_knot_registered_as_speed():
    """节=航速单位（声呐语境）：1 kn = 1852 m/h 精确换算，不当计数词。"""
    q = parse_quantity("3 节")
    assert q.kind == "speed"
    assert units.convert(q.value, q.unit, "m/s") == pytest.approx(1.5433, abs=1e-3)


def test_dimensionless_symbol_rejects_united_input():
    assert "n" not in formulas.available_inputs({"阵元数": "20 mm"})


def test_parse_quantity_multi_number_raises():
    with pytest.raises(UnresolvableQuantity):
        parse_quantity("20-30 米")


def test_parse_quantity_unknown_unit_raises():
    with pytest.raises(UnresolvableQuantity):
        parse_quantity("5 巴")
    with pytest.raises(UnresolvableQuantity):
        parse_quantity("30 °")
    with pytest.raises(UnresolvableQuantity):
        parse_quantity("100（设计值）")  # 数字后未识别内容不静默丢弃


def test_convert_cross_family_raises():
    with pytest.raises(UnresolvableQuantity):
        units.convert(20.0, "mm", "db")


# ----------------------------------------------------------------------
# 收口与下游
# ----------------------------------------------------------------------
def test_unit_to_mm_single_source():
    """geometry_link.UNIT_TO_MM 收口至 units.LENGTH_TO_MM，且含旧四键超集。"""
    from agents.a3_cad import geometry_link

    assert geometry_link.UNIT_TO_MM is units.LENGTH_TO_MM
    for k, v in {"mm": 1.0, "cm": 10.0, "dm": 100.0, "m": 1000.0}.items():
        assert units.LENGTH_TO_MM[k] == v


def test_standards_value_bound_in_mm():
    """长度族参数 value 统一换算 mm 绑定（demo 军标条目长度口径）。"""
    from solver.standards import _symbol_table

    param = {"name": "换能器外径", "value": 0.02, "unit": "m", "derivation": ""}
    table = _symbol_table(param, [param])
    assert table["value"] == pytest.approx(20.0)  # 0.02 m → 20 mm


def test_intervals_pool_unit_conversion():
    pools = intervals.candidate_pools([
        {"id": "P-001", "name": "阵元间距", "value": "0.02 m", "source_type": "case"},
        {"id": "P-002", "name": "阵元间距", "value": "20 mm", "source_type": "case"},
        {"id": "P-003", "name": "阵元间距", "value": "20-30 米", "source_type": "estimated"},
    ])
    assert [c["value"] for c in pools["d"]] == pytest.approx([0.02, 0.02])  # 范围表达剔除


def test_symbol_unit_registry_complete():
    """注册表全部符号带机器可读单位登记（str 旧形态视为无单位）。"""
    for spec in formulas.REGISTRY.values():
        for sym in spec.symbols:
            assert isinstance(spec.symbols[sym], dict), f"{spec.key}.{sym} 缺单位登记"
