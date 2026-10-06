# -*- coding: utf-8 -*-
"""T12.4 单测：方案正文风险质检（CHK-DOC 三检查，warning 级不阻断）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent


def _ir2(doc: str, params: list[dict] | None = None) -> dict:
    return {"design_document": doc, "parameters": params or []}


def _param(pid="P-001", name="缆长", value=100, unit="mm", st="formula", ref="REG#x"):
    return {"id": pid, "name": name, "value": value, "unit": unit, "source_type": st,
            "source_ref": ref, "derivation": f"len={value} mm", "confidence": "high",
            "trace_id": "t"}


def _run(doc, params):
    agent = A4Agent()
    return agent.run(_ir2(doc, params), ir3=None)


def test_degraded_marker_detected():
    ir4 = _run("# 方案\n⚠ 降级模式：本文档由降级流程生成", [_param()])
    doc_issues = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-DOC-DEGRADED"]
    assert len(doc_issues) == 1 and doc_issues[0]["severity"] == "warning"


def test_param_ref_consistency():
    doc = "# 方案\n引用 P-001 与 P-009（表中无 009）"
    ir4 = _run(doc, [_param()])
    refs = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-DOC-PARAM-REF"]
    assert len(refs) == 1 and "P-009" in refs[0]["message"]
    # 正常引用不产问题
    ir4b = _run("# 方案\n引用 P-001", [_param()])
    assert not [i for i in ir4b["issues"] if i["constraint_id"] == "CHK-DOC-PARAM-REF"]


def test_source_ref_missing():
    params = [_param(pid="P-001", st="formula", ref="REG#x"),
              _param(pid="P-002", name="外径", st="standard", ref=""),
              _param(pid="P-003", name="间距", st="estimated", ref="")]
    ir4 = _run("# 方案 正文", params)
    missing = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-DOC-SOURCE-MISSING"]
    assert len(missing) == 1 and "P-002" in missing[0]["related_params"]  # estimated 不要求


def test_warning_level_does_not_fail_overall():
    """质检为 warning 级：即便全中也不翻转 overall/CSR（口径不回归）。
    直测 _check_document（run() 整链受 ir3=None 的几何完整性 error 干扰）。"""
    agent = A4Agent()
    issues = agent._check_document(
        {"design_document": "# 方案\n⚠ 降级模式 引用 P-009"},
        [_param(pid="P-001", st="standard", ref="")])
    assert issues and all(i["severity"] == "warning" for i in issues)
    assert {i["constraint_id"] for i in issues} == {
        "CHK-DOC-DEGRADED", "CHK-DOC-PARAM-REF", "CHK-DOC-SOURCE-MISSING"}


def test_empty_document_flagged():
    ir4 = _run("   ", [_param()])
    assert any(i["constraint_id"] == "CHK-DOC-DEGRADED" and "正文为空" in i["message"]
               for i in ir4["issues"])
