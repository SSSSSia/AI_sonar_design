# -*- coding: utf-8 -*-
"""T9.3 军标核对激活单测（PHASE-009 §2.3）。

两层：
- 纯单测（无 DB）：表达式安全求值（禁 builtins/import）、四分支（pass/violation/uncovered/invalid）、
  A4 计入 CSR 与 error+back_to_A2；
- DB 集成（PG 可达才跑，否则 skip）：真 kb_standards（V12 expr 列 + demo 条目）→ A4 全链路。
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent
from solver import standards
from solver.standards import StandardSpec, _evaluate


def _param(pid, name, value, source_type="standard", derivation="", unit="-"):
    return {
        "id": pid, "name": name, "value": value, "unit": unit,
        "source_type": source_type, "source_ref": "GJB（演示）",
        "derivation": derivation, "confidence": "medium",
        "trace_id": f"tr-{pid.lower()}",
    }


def _ir2(params):
    return {"design_document": "# 样例\n", "parameters": params}


def _ir3():
    return {
        "cadquery_script": "import cadquery as cq\n",
        "step_file": {"bucket": "step-models", "object_key": "s/sonar_model.step"},
        "features": [],
    }


@contextmanager
def _with_spec(spec):
    standards.REGISTRY_STANDARDS.append(spec)
    try:
        yield
    finally:
        standards.REGISTRY_STANDARDS.remove(spec)


# ----------------------------------------------------------------------
# 表达式安全与求值分支
# ----------------------------------------------------------------------
def test_evaluate_blocks_builtins_and_import():
    table = {"value": 1.0}
    verdict, detail = _evaluate("__import__('os').system('echo pwned')", table)
    assert verdict == "invalid"
    assert "解析失败" in detail


def test_evaluate_branches():
    table = {"outer_diameter": 300.0, "f0": 800.0}
    assert _evaluate("outer_diameter <= 325", table)[0] == "pass"
    assert _evaluate("outer_diameter <= 250", table)[0] == "violation"
    assert _evaluate("unknown_sym <= 1", table)[0] == "uncovered"
    assert _evaluate("outer_diameter", table)[0] == "invalid"  # 非布尔表达式
    assert _evaluate("(f0 >= 150) & (f0 <= 1500)", table)[0] == "pass"
    assert _evaluate("(f0 >= 1000) & (f0 <= 1500)", table)[0] == "violation"


def test_evaluate_violation_detail_carries_substituted_values():
    _, detail = _evaluate("outer_diameter <= 250", {"outer_diameter": 300.0})
    assert "outer_diameter=300" in detail


# ----------------------------------------------------------------------
# check_param 分流（内存规则注入）
# ----------------------------------------------------------------------
def test_check_param_expr_pass_and_violation():
    spec = StandardSpec(code="DEMO-T", name="外径演示", applies_to="外径", expr="outer_diameter <= 325")
    with _with_spec(spec):
        ok = standards.check_param(
            _param("P-1", "声呐罩外径", 300, derivation="outer_diameter=300"))
        assert ok["status"] == "pass"
        bad = standards.check_param(
            _param("P-2", "声呐罩外径", 350, derivation="outer_diameter=350"))
        assert bad["status"] == "violation"
        assert "outer_diameter=350" in bad["message"]


def test_check_param_uncovered_when_symbol_missing():
    spec = StandardSpec(code="DEMO-T", name="外径演示", applies_to="外径", expr="outer_diameter <= 325")
    with _with_spec(spec):
        r = standards.check_param(_param("P-1", "声呐罩外径", 300))  # derivation 无符号
        assert r["status"] == "uncovered"
        assert "outer_diameter" in r["message"]


def test_check_param_empty_applies_to_never_matches():
    """applies_to 为空的条目不得匹配任何参数（旧实现的 '' in name 全匹配缺陷已修）。"""
    spec = StandardSpec(code="DEMO-T", name="空适用对象", applies_to="", expr="value <= 1")
    with _with_spec(spec):
        r = standards.check_param(_param("P-1", "任意参数", 999))
        assert r["status"] == "pending"


# ----------------------------------------------------------------------
# A4 集成：standard 检查计入 CSR / violation 出 error+back_to_A2
# ----------------------------------------------------------------------
def test_a4_standard_violation_counts_into_csr():
    spec = StandardSpec(code="DEMO-T", name="外径演示", applies_to="外径", expr="outer_diameter <= 325")
    with _with_spec(spec):
        params = [
            _param("P-1", "声呐罩外径", 300, derivation="outer_diameter=300"),
            _param("P-2", "基阵外径", 350, derivation="outer_diameter=350"),
        ]
        ir4 = A4Agent().run(_ir2(params), _ir3())
        assert ir4["overall"] == "fail"
        assert ir4["csr"] == 50.0, "军标核对 1 通过 + 1 违反 → 50.00"
        issue = next(i for i in ir4["issues"] if i["constraint_id"] == "CHK-STD-VIOLATION")
        assert issue["severity"] == "error"
        assert issue["suggestion"] == "back_to_A2"
        assert "outer_diameter=350" in issue["message"]


def test_a4_standard_uncovered_is_info_not_counted():
    spec = StandardSpec(code="DEMO-T", name="外径演示", applies_to="外径", expr="outer_diameter <= 325")
    with _with_spec(spec):
        params = [_param("P-1", "声呐罩外径", 300)]  # 无符号 → uncovered
        ir4 = A4Agent().run(_ir2(params), _ir3())
        assert ir4["csr"] is None, "未覆盖不算违反也不算通过，无有效检查 → null"
        assert any(i["constraint_id"] == "CHK-STD-UNCOVERED" and i["severity"] == "info"
                   for i in ir4["issues"])


def test_a4_symbol_from_other_param_derivation():
    """表达式符号可来自 IR2 其他参数的 derivation（跨参数约束）。"""
    spec = StandardSpec(code="DEMO-T", name="阵元间距演示", applies_to="阵元间距", expr="element_spacing >= 0.5")
    with _with_spec(spec):
        params = [
            _param("P-0", "结构记录", 0, source_type="estimated",
                   derivation="element_spacing=0.75, n=64"),
            _param("P-1", "阵元间距", 0.75, derivation="按结构记录登记", unit="m"),
        ]
        ir4 = A4Agent().run(_ir2(params), _ir3())
        assert ir4["csr"] == 100.0, "间距条目经 P-0 的符号表判定通过"


# ----------------------------------------------------------------------
# DB 集成：真 kb_standards（V12 expr 列 + demo 条目）
# ----------------------------------------------------------------------
def _pg_reachable() -> bool:
    try:
        from storage.registry_store import list_rows

        list_rows("standards")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_reachable(), reason="PG 不可达（未 source .env 或库未启动）")
class TestWithLiveStandards:
    def test_live_demo_entries_drive_a4(self):
        """真库 demo 条目（DEMO-STD-005 value >= -201 等）参与 A4：违反分支 + CSR 计数。"""
        standards.reset_cache()
        try:
            specs = standards.load_enabled_standards(max_age_s=0)
            assert any(s.code == "DEMO-STD-005" for s in specs), "缺 demo 条目（先跑 deploy/seed/demo_standards.sql）"
        finally:
            standards.reset_cache()

        params = [
            _param("P-1", "接收灵敏度", -198, derivation="按演示条目登记", unit="dB"),  # value=-198 >= -201 → pass
            _param("P-2", "发射灵敏度", -205, derivation="按演示条目登记", unit="dB"),  # value=-205 → violation
        ]
        ir4 = A4Agent().run(_ir2(params), _ir3())
        assert ir4["csr"] == 50.0, "真库条目：1 通过 + 1 违反 → 50.00"
        issue = next(i for i in ir4["issues"] if i["constraint_id"] == "CHK-STD-VIOLATION")
        assert "DEMO-STD-005" in issue["message"]
        assert issue["suggestion"] == "back_to_A2"
