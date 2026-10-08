# -*- coding: utf-8 -*-
"""T14.7 单测：公式依赖重算与 A4 复算取数源切换（外部审查问题 8）。

回归验收案例 17：改一参（n 16→32）→ 下游公式参数自动重算（DI 12.04→15.05），
且 A4 复算用新值——过期公式参数不再被旧 derivation 掩护。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent
from cad_agent.param_proposals import apply_forward_update, cascade_dependents
from solver.deps import downstream_order, snapshot_inputs
from solver.formulas import REGISTRY

KB_REF = "towed_array_cad_knowledge.txt#声学约束公式"


def _ir2():
    """n=16 上游 + DI 公式参数（旧值 12.04，derivation 记 n=16）+ 无关参数。"""
    return {"design_document": "# 方案", "parameters": [
        {"id": "P-001", "name": "阵元总数", "value": 16, "unit": "-", "source_type": "estimated",
         "source_ref": "IR1.objectives", "derivation": "用户需求显式给出 16",
         "confidence": "high", "trace_id": "t1"},
        {"id": "P-002", "name": "指向性指数 DI", "value": 12.0412, "unit": "dB",
         "source_type": "formula", "source_ref": f"{KB_REF}#DI≈10lg(N)",
         "derivation": "指向性指数 DI = n=16 → 代入计算得 12.0412 dB",
         "confidence": "high", "trace_id": "t2"},
        {"id": "P-003", "name": "缆长", "value": 100, "unit": "mm", "source_type": "estimated",
         "source_ref": "任务书", "derivation": "len=100 mm", "confidence": "medium", "trace_id": "t3"},
    ]}


def test_downstream_order_finds_formula_param():
    order = downstream_order(_ir2()["parameters"], ["P-001"])
    assert order == ["P-002"]


def test_downstream_ignores_unrelated_and_dynamic_formulas():
    params = _ir2()["parameters"]
    params[2]["source_type"] = "formula"
    params[2]["source_ref"] = "业务库#di_custom"  # registry 无命中——不建边
    order = downstream_order(params, ["P-001", "P-003"])
    assert order == ["P-002"]  # P-001 的下游 DI 在列；动态公式 P-003 不建边不重算


def test_case17_forward_update_cascades_di():
    """n 16→32：DI 自动重算 12.0412→15.053，derivation 结构化重建+依赖重算留痕。"""
    ir2 = _ir2()
    new_ir2 = apply_forward_update(ir2, "P-001", 32, "任务书修订")
    cascaded = cascade_dependents(new_ir2, ["P-001"], "E1 正向更新（P-001）")
    assert len(cascaded) == 1
    c = cascaded[0]
    assert c["param_id"] == "P-002" and c["formula_key"] == "directivity_index"
    assert c["old_value"] == 12.0412 and c["new_value"] == 15.0515  # 10*lg(32)
    di = next(p for p in new_ir2["parameters"] if p["id"] == "P-002")
    assert di["value"] == 15.0515
    assert "依赖重算" in di["derivation"] and "n=32" in di["derivation"]
    # 无关参数不动
    assert next(p for p in new_ir2["parameters"] if p["id"] == "P-003")["value"] == 100


def test_snapshot_inputs_unit_aware():
    """输入解析带单位换算：间距参数 "20 mm" → d(m)=0.02。"""
    params = [
        {"id": "P-001", "name": "阵元间距", "value": "20 mm", "unit": "mm"},
        {"id": "P-002", "name": "有效波束成形频率上限", "value": 37500, "unit": "Hz",
         "source_type": "formula", "source_ref": f"{KB_REF}#f_beam≤c/(2d)", "derivation": ""},
    ]
    spec = REGISTRY["beamforming_freq_max"]
    inputs = snapshot_inputs(params, params[1], spec)
    assert inputs == {"d": 0.02}  # c 缺省不入 inputs（required_symbols 之外）


def test_case17_a4_uses_snapshot_not_stale_derivation():
    """A4 取数源切换：上游已 32、DI 落库仍 12.04（过期）→ 复算用快照 n=32 →
    mismatch error 暴露过期值（旧逻辑解析旧 derivation n=16 会误判一致）。"""
    params = [
        {"id": "P-001", "name": "阵元总数", "value": 32, "unit": "-", "source_type": "estimated",
         "source_ref": "IR1", "derivation": "需求 32", "confidence": "high", "trace_id": "t1"},
        {"id": "P-002", "name": "指向性指数 DI", "value": 12.0412, "unit": "dB",
         "source_type": "formula", "source_ref": f"{KB_REF}#DI≈10lg(N)",
         "derivation": "指向性指数 DI = n=16 → 代入计算得 12.0412 dB",
         "confidence": "high", "trace_id": "t2"},
    ]
    ir4 = A4Agent().run({"design_document": "# x", "parameters": params}, None)
    err = [i for i in ir4["issues"] if i["constraint_id"] == "CHK-FORMULA-RECOMPUTE"
           and i["severity"] == "error"]
    assert len(err) == 1 and "参数快照" in err[0]["message"]


def test_a4_snapshot_consistent_pass():
    """级联后的自洽态：DI=15.053、上游 32 → 复算一致静默通过（csr 计入）。"""
    params = [
        {"id": "P-001", "name": "阵元总数", "value": 32, "unit": "-", "source_type": "estimated",
         "source_ref": "IR1", "derivation": "需求 32", "confidence": "high", "trace_id": "t1"},
        {"id": "P-002", "name": "指向性指数 DI", "value": 15.0515, "unit": "dB",
         "source_type": "formula", "source_ref": f"{KB_REF}#DI≈10lg(N)",
         "derivation": "依赖重算得", "confidence": "high", "trace_id": "t2"},
    ]
    ir4 = A4Agent().run({"design_document": "# x", "parameters": params}, None)
    assert ir4["csr"] == 100.0
    assert not [i for i in ir4["issues"] if i["constraint_id"] == "CHK-FORMULA-RECOMPUTE"
                and i["severity"] == "error"]
