"""T7.3 A3 几何特征真实化单测（PHASE-007）。

- geometry_link.link_params 纯函数：全联动 / 部分联动 / 全未联动 / 容差关闭；
- A3Agent._build_ir3 集成（mock 提取器，OCCT 不进 agent 进程）：extracted / 提取失败 /
  提取禁用三分支；产物全部过 IR3 新契约；subprocess 走 geometry_inspect CLI。
LLM 不参与（_build_ir3 不调用 LLM）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a3_cad.agent import A3Agent, GEOMETRY_INSPECT_CLI
from agents.a3_cad.geometry_link import link_params
from ir_contract import validate_ir

STEP_REF = {"bucket": "step-models", "object_key": "s1/sonar_model.step"}
SCRIPT_REF = {"bucket": "cad-scripts", "object_key": "s1/generated_script.py"}

PARAMS = [
    {"id": "P-001", "name": "湿端段直径", "value": 54, "unit": "mm",
     "source_type": "case", "source_ref": "K-1", "derivation": "d", "trace_id": "tr-1"},
    {"id": "P-002", "name": "阵列孔径", "value": 23.5, "unit": "m",
     "source_type": "formula", "source_ref": "F-1", "derivation": "d", "trace_id": "tr-2"},
    {"id": "P-003", "name": "工作深度", "value": 300, "unit": "m",
     "source_type": "estimated", "source_ref": "est", "derivation": "d", "trace_id": "tr-3"},
]

# 提取器输出：三段（Ø54×480、Ø54×23500、Ø40×1200）
PARTS = [
    {"index": 1, "center_mm": [0, 0, 240], "bbox_mm": [54.0, 54.0, 480.0], "volume_mm3": 1099320.0},
    {"index": 2, "center_mm": [0, 0, 12230], "bbox_mm": [54.0, 54.0, 23500.0], "volume_mm3": 53823244.0},
    {"index": 3, "center_mm": [0, 0, 28840], "bbox_mm": [40.0, 40.0, 1200.0], "volume_mm3": 1507964.0},
]
INSPECTED = {
    "ok": True,
    "overall": {"bbox_mm": [54.0, 54.0, 29440.0], "volume_mm3": 56430528.0, "part_count": 3},
    "parts": PARTS,
    "meta": {"extractor": "cadquery-occt/trimesh", "tolerance_mm": 1.0,
             "elapsed_s": 0.4, "segmentation": "radius_profile"},
}


def _agent() -> A3Agent:
    agent = A3Agent(llm=None)
    # 固定联动容差，避免环境变量影响断言
    agent._inspect_step = lambda path: INSPECTED  # type: ignore[method-assign]
    return agent


# ------------------------------------------------------------------------------
# link_params 纯函数
class TestLinkParams:
    def test_full_and_dim_selection(self):
        """直径 54 命中段 1 的 dim0（偏差 0%）；孔径 23.5m=23500mm 命中段 2 的 dim2
        （偏差 0%）；深度 300m=300000mm 无任何维度命中（如实不联动）。"""
        links = link_params(PARAMS, PARTS, tol_pct=20.0)
        assert links[1] == {"param_id": "P-001", "dim": 0}
        assert links[2] == {"param_id": "P-002", "dim": 2}
        assert 3 not in links  # 工作深度是量程，不是几何维度——绝不造假联动

    def test_one_param_takes_best_part(self):
        """一参多段命中取偏差最小段：20m 长度参数对 23500 与 240 段都超 20%？改用
        24000 参数：对 23500 偏差 2.1%，对 480 偏差 4900%——必选前者。"""
        params = [{"id": "P-L", "name": "阵长", "value": 24.0, "unit": "m",
                   "source_type": "case", "source_ref": "K", "derivation": "d", "trace_id": "t"}]
        links = link_params(params, PARTS, tol_pct=20.0)
        assert links[2]["param_id"] == "P-L"

    def test_one_part_one_param(self):
        """一段只联一参：两个参数都只命中段 1 的 dim0 时，偏差最小者胜出。"""
        params = [PARAMS[0],
                  {"id": "P-009", "name": "外径近似", "value": 60, "unit": "mm",
                   "source_type": "case", "source_ref": "K", "derivation": "d", "trace_id": "t"}]
        links = link_params(params, [PARTS[0]], tol_pct=20.0)
        assert links[1]["param_id"] == "P-001"  # 0% < 11.1%

    def test_tol_zero_disables(self):
        assert link_params(PARAMS, PARTS, tol_pct=0.0) == {}

    def test_non_length_units_skipped(self):
        params = [{"id": "P-X", "name": "频率", "value": 12, "unit": "kHz",
                   "source_type": "case", "source_ref": "K", "derivation": "d", "trace_id": "t"}]
        assert link_params(params, PARTS, tol_pct=99.0) == {}


# ------------------------------------------------------------------------------
# A3Agent._build_ir3 集成（mock 提取器）
class TestBuildIr3:
    def test_extracted_branch(self):
        ir3 = _agent()._build_ir3("script", STEP_REF, SCRIPT_REF,
                                  {"parameters": PARAMS}, "/tmp/x.step", fix_round=1)
        ir3 = validate_ir("IR3", ir3)
        assert ir3["geometry_summary"]["source"] == "extracted"
        assert ir3["geometry_summary"]["part_count"] == 3
        assert len(ir3["features"]) == 3  # features 数 = 实测段数，不再按参数数镜像
        f1, f2, f3 = ir3["features"]
        assert f1["segment"] == "SEG-01" and f1["linked_param_id"] == "P-001"
        assert f1["geometry"]["bbox_mm"] == [54.0, 54.0, 480.0]
        assert f1["geometry"]["linked_dim"] == 0
        assert f2["linked_param_id"] == "P-002"
        assert f3["linked_param_id"] is None  # 如实未联动
        assert "linked_dim" not in f3["geometry"]

    def test_extraction_failed_branch(self):
        agent = A3Agent(llm=None)
        agent._inspect_step = lambda path: {"ok": False, "error": "OCCT boom"}  # type: ignore
        ir3 = agent._build_ir3("script", STEP_REF, SCRIPT_REF,
                               {"parameters": PARAMS}, "/tmp/x.step", fix_round=0)
        ir3 = validate_ir("IR3", ir3)
        assert ir3["geometry_summary"]["source"] == "extraction_failed"
        assert "OCCT boom" in ir3["geometry_summary"]["detail"]
        assert ir3["features"] == []  # 管线继续，不造假特征

    def test_disabled_branch(self):
        agent = A3Agent(llm=None)
        agent._inspect_step = lambda path: None  # type: ignore
        ir3 = agent._build_ir3("script", STEP_REF, SCRIPT_REF,
                               {"parameters": PARAMS}, "/tmp/x.step", fix_round=0)
        ir3 = validate_ir("IR3", ir3)
        assert ir3["geometry_summary"]["source"] == "extraction_failed"
        assert "禁用" in ir3["geometry_summary"]["detail"]
        assert ir3["features"] == []

    def test_no_occt_in_agent_process(self, monkeypatch):
        """OCCT 镶嵌禁止进入 agent 进程：提取只能经 subprocess 调 CLI（mock 捕获命令），
        且 agent 模块源码不含 cadquery 导入（全量回归时其他测试可能已加载 cadquery，
        故断言源码而非 sys.modules）。"""
        import ast

        called = {}

        def fake_run(cmd, **kwargs):
            called["cmd"] = list(cmd)
            out = cmd[3]
            Path(out).write_text(
                '{"ok": true, "overall": {"bbox_mm": [1,1,1], "volume_mm3": 1.0, "part_count": 1},'
                ' "parts": [{"index": 1, "center_mm": [0,0,0], "bbox_mm": [1,1,1], "volume_mm3": 1.0}],'
                ' "meta": {"extractor": "x", "tolerance_mm": 1, "elapsed_s": 0.1, "segmentation": "fallback_single"}}',
                encoding="utf-8",
            )

        import subprocess as sp

        monkeypatch.setattr(sp, "run", fake_run)
        agent = A3Agent(llm=None)
        agent._build_ir3("script", STEP_REF, SCRIPT_REF, {"parameters": []}, "/tmp/never.step", 0)
        assert Path(called["cmd"][1]).resolve() == GEOMETRY_INSPECT_CLI.resolve()  # 走 CLI 子进程

        source = Path(A3Agent.__module__.replace(".", "/") + ".py").resolve()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("cadquery" not in a.name and "OCP" not in a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module and ("cadquery" in node.module or "OCP" in node.module))
