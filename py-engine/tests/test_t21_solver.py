# -*- coding: utf-8 -*-
"""T2.1 solver 底座单测：SymPy 复算互证 / derivation 解析 / 区间传播 / 军标框架占位。"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solver import formulas, intervals, recompute, standards


# ----------------------------------------------------------------------
# SymPy 表达式底座
# ----------------------------------------------------------------------

def test_registry_has_sympy_expr_for_all_formulas():
    for key, spec in formulas.REGISTRY.items():
        assert spec.expr, f"{key} 缺少 SymPy 表达式"
        # 表达式可解析且只引用声明过的符号
        parsed = __import__("sympy").sympify(spec.expr)
        free = {str(s) for s in parsed.free_symbols}
        assert free <= set(spec.symbols), f"{key} 表达式引用了未声明符号: {free}"


def test_recompute_matches_math_closure_on_all_formulas():
    """互证：SymPy 复算与 A2 在用的 math 闭包同输入同输出（round4 精度内）。"""
    cases = [
        ("directivity_index", {"n": 16}, 16),
        ("beamforming_freq_max", {"d": 1.5}, 1.5),
        ("transmission_loss_spherical", {"r": 300.0}, 300.0),
        ("transmission_loss_cylindrical", {"r": 300.0}, 300.0),
        ("sonar_equation_di_required", {"sl": 180.0, "nl": 55.0, "tl": 49.5}, 180.0),
    ]
    for key, inputs, _ in cases:
        spec = formulas.REGISTRY[key]
        kwargs = dict(inputs)
        kwargs.update(spec.defaults or {})
        expected = spec.compute(**kwargs)
        result = recompute.recompute(key, inputs)
        assert recompute.values_match(result.value, expected), (key, result.value, expected)
        assert result.unit == spec.unit
        assert spec.name in result.derivation


def test_recompute_exact_rational_input():
    """Rational 精确代入：DI=10lg(16) 应与解析值一致到 1e-9。"""
    result = recompute.recompute("directivity_index", {"n": 16})
    assert abs(result.value - 10 * math.log10(16)) < 1e-9


def test_recompute_missing_required_symbol_raises():
    import pytest

    with pytest.raises(KeyError):
        recompute.recompute("directivity_index", {})


def test_parse_inputs_from_real_derivation():
    """从 evaluate_available 固化的 derivation 格式解析代入值（阶段 1 e2e 同款格式）。"""
    known = {"阵元总数": 16, "阵元间距": 1.5}
    entries = formulas.evaluate_available(known)
    di = next(e for e in entries if e["formula_key"] == "directivity_index")
    inputs = recompute.parse_inputs_from_derivation(di["derivation"])
    assert inputs["n"] == 16
    result = recompute.recompute("directivity_index", inputs)
    assert recompute.values_match(result.value, di["value"])


def test_resolve_formula_key_by_source_ref():
    spec = formulas.REGISTRY["directivity_index"]
    assert recompute.resolve_formula_key(spec.source_ref) == "directivity_index"
    assert recompute.resolve_formula_key("未知出处#别的公式") is None
    assert recompute.resolve_formula_key("") is None


def test_values_match_tolerances():
    assert recompute.values_match(12.0412, 12.0411998266)  # round4 误差内
    assert not recompute.values_match(12.0412, 13.0)
    assert recompute.values_match(1000000.0, 1000000.5)  # 大值相对容差


# ----------------------------------------------------------------------
# 区间传播
# ----------------------------------------------------------------------

def _param(pid, name, value, source_type):
    return {"id": pid, "name": name, "value": value, "source_type": source_type}


def test_candidate_pools_collects_case_and_estimated_only():
    params = [
        _param("P-001", "阵元总数", 16, "estimated"),
        _param("P-002", "阵元总数", 32, "case"),
        _param("P-003", "阵元间距", 1.5, "case"),
        _param("P-004", "指向性指数 DI", 12.04, "formula"),  # 不进池
        _param("P-005", "别的量", "abc", "case"),  # 非数值不进池
    ]
    pools = intervals.candidate_pools(params)
    assert set(pools) == {"n", "d"}
    assert sorted(c["value"] for c in pools["n"]) == [16.0, 32.0]


def test_propagate_interval_di_two_candidates():
    pools = intervals.candidate_pools([_param("P-001", "阵元总数", 16, "case"),
                                       _param("P-002", "阵元总数", 32, "case")])
    interval = intervals.propagate_interval("directivity_index", pools)
    assert interval is not None
    low, high = interval
    assert abs(low - 10 * math.log10(16)) < 1e-9
    assert abs(high - 10 * math.log10(32)) < 1e-9


def test_propagate_interval_missing_symbol_returns_none():
    pools = intervals.candidate_pools([_param("P-001", "声速", 1500, "case")])
    assert intervals.propagate_interval("beamforming_freq_max", pools) is None


def test_propagate_interval_uses_default_when_optional_absent():
    pools = intervals.candidate_pools([_param("P-001", "阵元间距", 1.5, "case")])
    interval = intervals.propagate_interval("beamforming_freq_max", pools)
    assert interval == (500.0, 500.0)  # 默认 c=1500


def test_case_conflicts_detected_and_within_tolerance_ignored():
    params = [_param("P-001", "阵元总数", 16, "case"), _param("P-002", "阵元总数", 32, "case"),
              _param("P-003", "阵元间距", 1.5, "case"), _param("P-004", "阵元间距", 1.6, "case")]
    pools = intervals.candidate_pools(params)
    conflicts = intervals.case_conflicts(pools)
    symbols = {c["symbol"] for c in conflicts}
    assert symbols == {"n"}
    conflict = next(c for c in conflicts if c["symbol"] == "n")
    assert set(conflict["param_ids"]) == {"P-001", "P-002"}


# ----------------------------------------------------------------------
# 军标框架占位
# ----------------------------------------------------------------------

def test_standards_empty_registry_reports_pending_honestly():
    result = standards.check_param(_param("S-1", "换能器灵敏度", -195, "standard"))
    assert result["status"] == "pending"
    assert "待补" in result["message"]


def test_standards_matched_entry_with_check():
    spec = standards.StandardSpec(
        code="GJB-TEST", name="测试军标", applies_to="灵敏度",
        check=lambda p: None if float(p["value"]) > -200 else "灵敏度不得低于 -200dB",
    )
    standards.REGISTRY_STANDARDS.append(spec)
    try:
        ok = standards.check_param(_param("S-1", "换能器灵敏度", -195, "standard"))
        assert ok["status"] == "pass"
        bad = standards.check_param(_param("S-2", "换能器灵敏度", -210, "standard"))
        assert bad["status"] == "violation" and "-200" in bad["message"]
        miss = standards.check_param(_param("S-3", "别的参数", 1, "standard"))
        assert miss["status"] == "pending"
    finally:
        standards.REGISTRY_STANDARDS.remove(spec)
