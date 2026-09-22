"""T1.4/T1.5 单测：solver 公式库、A2 契约、A3 校验链与沙箱。LLM 全 mock。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a2_scheme.agent import A2Agent
from cad_engine import sandbox, validators
from ir_contract import IrContractError, validate_ir
from solver import formulas


class TestSolverFormulas:
    def test_di_10lg_n(self):
        r = formulas.evaluate_available({"n": 32})[0]
        assert r["source_type"] == "formula"
        assert abs(r["value"] - 10 * math.log10(32)) < 1e-6
        assert "声学约束公式" in r["source_ref"]

    def test_fbeam_from_spacing(self):
        r = {x["formula_key"]: x for x in formulas.evaluate_available({"d": 1.5})}
        assert r["beamforming_freq_max"]["value"] == pytest.approx(500.0)

    def test_missing_inputs_skips(self):
        keys = {r["formula_key"] for r in formulas.evaluate_available({"r": 5000})}
        assert "transmission_loss_spherical" in keys
        assert "directivity_index" not in keys  # n 缺失则不算，不编造

    def test_alias_mapping(self):
        keys = {r["formula_key"] for r in formulas.evaluate_available({"阵元数": 16, "阵元间距": 1.5})}
        assert "directivity_index" in keys and "beamforming_freq_max" in keys

    def test_computed_value_is_code_only(self):
        """A-07：公式数值由代码算出，derivation 含代入过程可复算。"""
        r = {x["formula_key"]: x for x in formulas.evaluate_available({"n": 16})}
        di = r["directivity_index"]
        assert di["derivation"].count("=") >= 1
        assert abs(di["value"] - 12.0412) < 1e-3


GOOD_IR1 = {
    "objectives": [
        {"dimension": "阵元数", "value": 16, "explicit": True},
        {"dimension": "阵元间距", "value": 1.5, "explicit": True},
    ],
    "missing_items": ["频段未明确"],
    "sub_queries": ["q1", "q2", "q3", "q4"],
    "retrieved_context": [
        {"doc_id": "kb.txt", "location": "段落1", "snippet": "声阵段外径 27mm，壁厚 3mm。"}
    ],
}


class FakeLlmA2:
    """参数提取返回 retrieved+proposed；文档整合返回固定 markdown。"""

    def chat(self, messages, **kwargs):
        user = messages[-1]["content"]
        if "JSON 数组" in user:
            return (
                '[{"name":"声阵段外径","value":27,"unit":"mm","source":"retrieved","rationale":"kb.txt 段落1 原文"},'
                '{"name":"总长度","value":999,"unit":"m","source":"proposed","rationale":"按 16 阵元×1.5m 估算"}]'
            )
        return "# 测试系统 CAD 设计文档\n\n## 1. 项目概述\n测试。"


class TestA2Contract:
    def test_full_path_valid_ir2(self):
        ir2 = A2Agent(llm=FakeLlmA2()).run(GOOD_IR1)
        validate_ir("IR2", ir2)
        types = {p["source_type"] for p in ir2["parameters"]}
        assert "case" in types and "estimated" in types and "formula" in types
        for p in ir2["parameters"]:
            assert p["trace_id"].startswith("tr-")
            assert p["source_ref"] and p["derivation"]
        ids = [p["id"] for p in ir2["parameters"]]
        assert ids[0] == "P-001" and len(set(ids)) == len(ids)
        # A-07：公式值来自代码（DI=10lg16）
        di = next(p for p in ir2["parameters"] if p["name"] == "指向性指数 DI")
        assert abs(di["value"] - 10 * math.log10(16)) < 1e-3

    def test_llm_down_degraded_but_valid(self):
        class Down:
            def chat(self, *a, **k):
                raise RuntimeError("down")

        ir2 = A2Agent(llm=Down()).run(GOOD_IR1)
        validate_ir("IR2", ir2)
        assert "降级模式" in ir2["design_document"]
        assert ir2["parameters"]  # 公式参数 + 显式目标仍在
        di = next(p for p in ir2["parameters"] if p["name"] == "指向性指数 DI")
        assert di["source_type"] == "formula"


GOOD_SCRIPT = """
import math, os
import cadquery as cq
from cadquery import Color

def get_spline_pts(length, amplitude, z_start=0):
    pts = []
    for i in range(11):
        pct = i / 10.0
        z = z_start + length * pct
        x = amplitude * (math.sin(math.pi * pct) ** 2)
        pts.append((x, z))
    return pts

part = cq.Workplane("XY").circle(13.5).extrude(100.0)
assy = cq.Assembly()
assy.add(part, name="body")
os.makedirs(OUTPUT_DIR, exist_ok=True)
assy.save(os.path.join(OUTPUT_DIR, "sonar_model.step"), "STEP")
"""


class TestValidators:
    def test_chain_ok(self):
        code, ok, errors, warnings = validators.validate_chain(GOOD_SCRIPT)
        assert ok and not errors

    def test_sin_auto_fix(self):
        bad = GOOD_SCRIPT.replace("math.sin(math.pi * pct) ** 2", "math.sin(math.pi * pct)")
        code, ok, errors, warnings = validators.validate_chain(bad)
        assert "** 2" in code  # 自动修复

    def test_missing_markers_rejected(self):
        code, ok, errors, _ = validators.validate_chain("x = 1")
        assert not ok and any("缺少" in e for e in errors)

    def test_syntax_error(self):
        code, ok, errors, _ = validators.validate_chain("def broken(:\n    pass")
        assert not ok and any("语法" in e for e in errors)

    def test_color_range_auto_fix(self):
        """2026-09-22 e2e 实测：LLM 把 RGB 写成 0–255 导致 Standard_OutOfRange 且修复
        循环无法自愈——确定性归一化，颜色错误不允许消耗修复轮次。"""
        code = validators.auto_fix_patterns(
            "c1 = Color(51, 102, 153)\nc2 = Color(0.2, 0.4, 0.6)\n"
            "c3 = Color('steelblue')\nc4 = assy.add(p, color=Color(200, 200, 200))"
        )
        assert "Color(0.2000, 0.4000, 0.6000)" in code  # 51/255, 102/255, 153/255
        assert "Color(0.2, 0.4, 0.6)" in code           # 已是 0–1 不动
        assert "Color('steelblue')" in code              # 命名色不动
        assert "Color(0.7843, 0.7843, 0.7843)" in code   # 关键字参数内的调用同样修复
        assert "(51," not in code and "200, 200, 200" not in code


class TestSandbox:
    def test_happy_path_step_produced(self):
        r = sandbox.run_in_subprocess(GOOD_SCRIPT, time_limit_s=120)
        assert r["ok"], r["error"]
        assert len(r["step_files"]) == 1
        assert r["step_files"][0].stat().st_size > 1000

    def test_runtime_error_reported(self):
        r = sandbox.run_in_subprocess("raise ValueError('boom')", time_limit_s=60)
        assert not r["ok"]
        assert "ValueError" in r["error"]

    def test_independent_workdir(self):
        """宪法 §10.3：独立工作目录。"""
        r = sandbox.run_in_subprocess(GOOD_SCRIPT, time_limit_s=120)
        assert str(r["workdir"]) in str(r["step_files"][0])

    def test_timeout_killed(self):
        r = sandbox.run_in_subprocess("import time; time.sleep(60)", time_limit_s=5)
        assert not r["ok"] and "超时" in r["error"]

    def test_namespace_prelude(self):
        """脚本不 import 也可用（cq/math/OUTPUT_DIR 预置）。"""
        script = """
part = cq.Workplane("XY").circle(10).extrude(50)
assy = cq.Assembly()
assy.add(part, name="p")
os.makedirs(OUTPUT_DIR, exist_ok=True)
assy.save(os.path.join(OUTPUT_DIR, "m.step"), "STEP")
"""
        r = sandbox.run_in_subprocess(script, time_limit_s=120)
        assert r["ok"], r["error"]
