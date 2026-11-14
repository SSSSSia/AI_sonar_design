# -*- coding: utf-8 -*-
"""T15.1 结构化参数注入与振幅条款治理单测（PHASE-015，ADR-014 §3）。

- prompts：对象声明注释通用节（@obj/@display）、振幅条款物理/显示分离、PROMPT_VERSION=3；
- agent._generate：完整 IR2 参数 JSON 注入（脚本取数唯一权威事实源）；
- identity.parse_display_transforms：@display 声明解析（畸形行容错跳过，A-16 不造值）；
- A3Agent._build_ir3：display_transforms 进 IR3（无声明省略字段=旧形态）；
- A4Agent._check_display_transform：CHK-DISPLAY-TRANSFORM info / 悬空引用 warning。
LLM 不参与（_generate 经 fake 捕获消息，不真调用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agents.a3_cad import identity
from agents.a3_cad.agent import A3Agent
from agents.a3_cad.prompts import (
    CADQUERY_BASE_PROMPT,
    DOMAIN_PROMPTS,
    PROMPT_VERSION,
    build_system_prompt,
)
from agents.a4_validation.agent import A4Agent
from ir_contract import validate_ir
from tests.test_t73_a3_geometry import INSPECTED, SCRIPT_REF, STEP_REF  # noqa: F401

# ------------------------------------------------------------------------------
# 提示词契约（对象声明节 + 振幅条款治理）
class TestPromptContract:
    def test_declaration_convention_in_base_prompt(self):
        """@obj/@display 语法节属通用层：任何领域（含未注册领域）的 system prompt 都有。"""
        for prompt in (CADQUERY_BASE_PROMPT, build_system_prompt(None), build_system_prompt("towed_array")):
            assert "# @obj" in prompt
            assert "# @display" in prompt
            assert "length|diameter|width|height|thickness|other" in prompt
            assert "elem-[01-16]" in prompt  # 区间语法示范

    def test_amplitude_clause_physical_display_split(self):
        """振幅条款治理（B-1）：视觉缩放必须 @display 声明 + 禁止静默缩小（A-16）。"""
        clause = DOMAIN_PROMPTS["towed_array"]
        assert "# @display" in clause
        assert "禁止静默缩小" in clause
        assert "physical_mm" in clause
        # 旧"静默使用"措辞不得残留（示例行不再只给视觉值而不要求声明）
        assert "实际使用 ±400~500mm" not in clause

    def test_prompt_version_bumped(self):
        """T15.1 文本变更 → PROMPT_VERSION 递增（A0-3 缓存指纹自动失效）。"""
        assert PROMPT_VERSION == "3"


# ------------------------------------------------------------------------------
# _generate 参数注入（fake LLM 捕获消息）
class _CaptureLLM:
    def __init__(self):
        self.messages: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.messages.append(messages)
        return "import math\n# stub"


class TestGenerateInjection:
    PARAMS = [
        {"id": "P-001", "name": "湿端段直径", "value": 54, "unit": "mm",
         "source_type": "case", "source_ref": "K-1", "derivation": "d1", "trace_id": "tr-1"},
        {"id": "P-002", "name": "阵列孔径", "value": 23.5, "unit": "m",
         "source_type": "formula", "source_ref": "F-1", "derivation": "d2", "trace_id": "tr-2"},
    ]

    def test_full_params_json_injected(self):
        llm = _CaptureLLM()
        agent = A3Agent(llm=llm)
        agent._generate("设计文档正文", self.PARAMS, "", None)
        user = llm.messages[0][1]["content"]
        assert "设计文档正文" in user
        assert "结构化参数表" in user and "唯一权威事实源" in user
        for p in self.PARAMS:  # 完整参数 JSON（含 derivation/trace_id，非节选）
            assert p["id"] in user and p["derivation"] in user and p["trace_id"] in user
        assert '"unit": "m"' in user or '"unit":"m"' in user

    def test_no_params_no_block(self):
        llm = _CaptureLLM()
        A3Agent(llm=llm)._generate("doc", [], "", None)
        assert "结构化参数表" not in llm.messages[0][1]["content"]


# ------------------------------------------------------------------------------
# parse_display_transforms 纯函数
class TestParseDisplay:
    def test_full_form(self):
        script = (
            "x = 1\n"
            "# @display param=P-AMP physical_mm=1500 visual_mm=450 rule=min(doc_amplitude,radius*40)\n"
            "y = 2\n"
        )
        out = identity.parse_display_transforms(script)
        assert out == [{"param_id": "P-AMP", "physical_mm": 1500.0, "visual_mm": 450.0,
                        "rule": "min(doc_amplitude,radius*40)"}]

    def test_param_none_and_optional_keys(self):
        out = identity.parse_display_transforms(
            "# @display param=none physical_mm=-400 visual_mm=-400 reason=视觉与物理一致\n"
        )
        assert out == [{"param_id": None, "physical_mm": -400.0, "visual_mm": -400.0,
                        "reason": "视觉与物理一致"}]

    def test_malformed_lines_skipped(self):
        """缺键/非法数值整行跳过（A-16 不造半截契约对象），合法行保留。"""
        out = identity.parse_display_transforms(
            "# @display param=P-AMP visual_mm=450\n"          # 缺 physical_mm
            "# @display param=P-AMP physical_mm=abc visual_mm=450\n"  # 数值非法
            "# @display physical_mm=1500 visual_mm=450\n"     # param 缺省 → None
        )
        assert out == [{"param_id": None, "physical_mm": 1500.0, "visual_mm": 450.0}]

    def test_no_declarations_empty(self):
        assert identity.parse_display_transforms("import math\nx = 1\n") == []
        assert identity.parse_display_transforms("") == []
        # 非 display 注释不误收
        assert identity.parse_display_transforms("# @obj a role=x\n# display param=1\n") == []


# ------------------------------------------------------------------------------
# _build_ir3 集成（复用 T7.3 mock 提取器夹具）
class TestBuildIr3Display:
    def test_display_transforms_emitted(self):
        script = (
            "import math\n"
            "# @display param=P-001 physical_mm=1500 visual_mm=450 rule=min(doc,r*40)\n"
            "assy.save('x.step')\n"
        )
        ir3 = validate_ir("IR3", A3Agent(llm=None)._build_ir3(
            script, STEP_REF, SCRIPT_REF, {"parameters": []}, "/tmp/x.step", fix_round=0))
        assert ir3["display_transforms"] == [
            {"param_id": "P-001", "physical_mm": 1500.0, "visual_mm": 450.0,
             "rule": "min(doc,r*40)"}]

    def test_no_declaration_field_omitted(self):
        """无声明 → 字段省略（旧形态，A-16 不造空契约）。"""
        ir3 = validate_ir("IR3", A3Agent(llm=None)._build_ir3(
            "import math\n", STEP_REF, SCRIPT_REF, {"parameters": []}, "/tmp/x.step", fix_round=0))
        assert "display_transforms" not in ir3


# ------------------------------------------------------------------------------
# A4 CHK-DISPLAY-TRANSFORM
def _ir2(params=None):
    return {"design_document": "d", "parameters": params or []}


class TestA4DisplayTransform:
    def test_info_for_declared_transform(self):
        ir3 = {"display_transforms": [
            {"param_id": "P-AMP", "physical_mm": 1500.0, "visual_mm": 450.0, "rule": "min(x,40r)"}]}
        issues = [i for i in A4Agent().run(_ir2([{"id": "P-AMP"}]), ir3)["issues"]
                  if i["constraint_id"] == "CHK-DISPLAY-TRANSFORM"]
        assert len(issues) == 1
        assert issues[0]["severity"] == "info"
        assert "450" in issues[0]["message"] and "min(x,40r)" in issues[0]["message"]
        assert issues[0]["related_params"] == ["P-AMP"]

    def test_dangling_param_reference_is_warning(self):
        ir3 = {"display_transforms": [{"param_id": "P-GONE", "physical_mm": 1500.0, "visual_mm": 450.0}]}
        issues = [i for i in A4Agent().run(_ir2(), ir3)["issues"]
                  if i["constraint_id"] == "CHK-DISPLAY-TRANSFORM"]
        assert issues[0]["severity"] == "warning"
        assert "P-GONE" in issues[0]["message"]

    def test_no_transforms_no_issue(self):
        issues = [i for i in A4Agent().run(_ir2(), {"features": []})["issues"]
                  if i["constraint_id"] == "CHK-DISPLAY-TRANSFORM"]
        assert issues == []
