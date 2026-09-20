# -*- coding: utf-8 -*-
"""T2.1 A4 合规校验引擎单测：四类 source_type 分流（含故意注入违例）+ 几何自洽 + 契约。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent
from ir_contract import load_schema, validate_ir

KB_REF = "towed_array_cad_knowledge.txt#声学约束公式"


# ----------------------------------------------------------------------
# 样例构造
# ----------------------------------------------------------------------

def _param(pid, name, value, source_type, source_ref, derivation, confidence="high", unit="-"):
    return {
        "id": pid, "name": name, "value": value, "unit": unit,
        "source_type": source_type, "source_ref": source_ref,
        "derivation": derivation, "confidence": confidence,
        "trace_id": f"tr-{pid.lower()}",
    }


def _ir2(params):
    return {"design_document": "# 样例\n", "parameters": params}


def _ir3(features=None, step=True, script="import cadquery as cq\n"):
    return {
        "cadquery_script": script,
        "step_file": (
            {"bucket": "step-models", "object_key": "s/sonar_model.step"}
            if step else {"bucket": "", "object_key": ""}
        ),
        "features": features or [],
    }


def _good_params():
    """全绿基线：估算目标 + 案例 + 正确公式值 + standard。"""
    return [
        _param("P-001", "阵元总数", 16, "estimated", "IR1.objectives",
               "用户需求显式给出（IR1 目标登记）"),
        _param("P-002", "阵元间距", 1.5, "case", "KB 案例：间距 1.5m",
               "检索知识明确给出的案例参数：间距 1.5m", unit="m"),
        _param("P-003", "指向性指数 DI", 12.0412, "formula",
               f"{KB_REF}#DI≈10lg(N)",
               "指向性指数 DI = n=16 → 代入计算得 12.0412 dB", unit="dB"),
        _param("P-004", "灵敏度", -195, "standard", "GJB（待入库）",
               "军标条目待入库，暂按案例登记", confidence="medium", unit="dB"),
    ]


def _severity(issues, constraint_id):
    return [i["severity"] for i in issues if i["constraint_id"] == constraint_id]


def _first(issues, constraint_id):
    return next(i for i in issues if i["constraint_id"] == constraint_id)


# ----------------------------------------------------------------------
# formula 分流
# ----------------------------------------------------------------------

def test_formula_correct_value_passes():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    assert ir4["overall"] == "pass"
    assert "error" not in [i["severity"] for i in ir4["issues"]]


def test_formula_tampered_value_is_error_back_to_a2():
    """违例注入：公式参数登记值被改（12.0412 → 13.5）必须 error 并建议回 A2。"""
    params = _good_params()
    params[2]["value"] = 13.5
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert ir4["overall"] == "fail"
    issue = _first(ir4["issues"], "CHK-FORMULA-RECOMPUTE")
    assert issue["severity"] == "error"
    assert issue["suggestion"] == "back_to_A2"
    assert "复算不符" in issue["message"]


def test_formula_unresolvable_source_ref_is_info_not_error():
    """无法定位注册公式的 formula 参数：info 请人工复核，不硬失败（注册表会增长）。"""
    params = _good_params()
    params[2]["source_ref"] = "自定义手册#某公式"
    params[2]["name"] = "自定义指标"  # 避开 kb_formulas 现网行名（环境耦合：名称精确匹配会命中真库公式）
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert _severity(ir4["issues"], "CHK-FORMULA-RECOMPUTE") == ["info"]
    assert ir4["overall"] == "pass"


def test_formula_non_numeric_value_is_error():
    params = _good_params()
    params[2]["value"] = "待定"
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert _severity(ir4["issues"], "CHK-FORMULA-RECOMPUTE") == ["error"]


# ----------------------------------------------------------------------
# case 分流
# ----------------------------------------------------------------------

def test_case_interval_violation_is_warning():
    """违例注入：DI 复算自洽（n=100）但案例候选只支持 n=32 → 区间外 warning。"""
    params = [
        _param("P-001", "阵元总数", 32, "case", "KB 案例：32 阵元",
               "检索知识明确给出的案例参数：32 阵元"),
        _param("P-002", "指向性指数 DI", 20.0, "formula",
               f"{KB_REF}#DI≈10lg(N)",
               "指向性指数 DI = n=100 → 代入计算得 20.0 dB", unit="dB"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert _severity(ir4["issues"], "CHK-CASE-INTERVAL") == ["warning"]
    # 复算自洽所以无 error
    assert ir4["overall"] == "pass"


def test_case_conflict_detected():
    """违例注入：同一符号两个差异 >10% 的候选（16 vs 32）→ warning 冲突。"""
    params = _good_params() + [
        _param("P-010", "阵元总数", 32, "case", "KB 案例：32 阵元拖曳阵",
               "检索知识明确给出的案例参数：32 阵元"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    conflict = _first(ir4["issues"], "CHK-CASE-CONFLICT")
    assert conflict["severity"] == "warning"
    assert set(conflict["related_params"]) == {"P-001", "P-010"}


def test_case_close_candidates_no_conflict():
    params = _good_params() + [
        _param("P-010", "阵元间距", 1.6, "case", "KB 案例：间距 1.6m",
               "检索知识明确给出的案例参数：间距 1.6m", unit="m"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert not [i for i in ir4["issues"] if i["constraint_id"] == "CHK-CASE-CONFLICT"]


# ----------------------------------------------------------------------
# standard / estimated 分流
# ----------------------------------------------------------------------

def test_standard_empty_registry_reports_pending_info():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    issue = _first(ir4["issues"], "CHK-STD-PENDING")
    assert issue["severity"] == "info"
    assert "待补" in issue["message"]


def test_estimated_summary_is_info_with_all_params():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    issue = _first(ir4["issues"], "CHK-EST-UNVERIFIED")
    assert issue["severity"] == "info"
    assert issue["related_params"] == ["P-001"]


def test_info_only_report_is_pass():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    assert ir4["overall"] == "pass"
    # info 级问题不允许携带回环建议（suggestion 可选性的业务面）
    assert all("suggestion" not in i for i in ir4["issues"] if i["severity"] == "info")


# ----------------------------------------------------------------------
# 几何自洽
# ----------------------------------------------------------------------

def test_geometry_dangling_link_is_error_back_to_a3():
    features = [{
        "feature_id": "F-001", "segment": "model",
        "geometry": {"linked_param": "P-999"}, "linked_param_id": "P-999",
    }]
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3(features=features))
    issue = _first(ir4["issues"], "CHK-GEO-LINK")
    assert issue["severity"] == "error"
    assert issue["suggestion"] == "back_to_A3"
    assert issue["related_features"] == ["F-001"]
    assert ir4["overall"] == "fail"


def test_geometry_duplicate_feature_id_is_error():
    f = {"feature_id": "F-001", "segment": "model", "geometry": {}, "linked_param_id": "P-001"}
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3(features=[f, dict(f)]))
    assert _severity(ir4["issues"], "CHK-GEO-DUP-FEATURE") == ["error"]


def test_geometry_missing_step_or_script_is_error():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3(step=False))
    assert _severity(ir4["issues"], "CHK-GEO-STEP") == ["error"]
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3(script=""))
    assert _severity(ir4["issues"], "CHK-GEO-SCRIPT") == ["error"]


# ----------------------------------------------------------------------
# 契约与聚合
# ----------------------------------------------------------------------

def test_valid_ir2_ir3_produce_valid_ir4_contract():
    """合法 IR2+IR3 → 合法 IR4（A4Agent 内部已 validate_ir，此处对 schema 直接再验）。"""
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    schema = load_schema("IR4")
    from jsonschema import validate as _v

    _v(ir4, schema)  # jsonschema.validate(instance, schema)
    with pytest.raises(Exception):
        _v({"overall": "pass"}, schema)  # 缺 issues
    from ir_contract import IrContractError

    with pytest.raises(IrContractError):
        validate_ir("IR4", {"overall": "no", "issues": []})  # overall 枚举外


def test_no_params_empty_report():
    ir4 = A4Agent().run(_ir2([]), _ir3())
    assert ir4 == {"overall": "pass", "issues": []}
