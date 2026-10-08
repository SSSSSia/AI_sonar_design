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


@pytest.fixture()
def no_standards(monkeypatch):
    """隔离业务库军标条目（demo 条目 applies_to 命中会引入环境耦合）。"""
    from solver import standards

    monkeypatch.setattr(standards, "REGISTRY_STANDARDS", [])
    monkeypatch.setattr(standards, "load_enabled_standards", lambda max_age_s=30: [])


# ----------------------------------------------------------------------
# formula 分流
# ----------------------------------------------------------------------

def test_formula_correct_value_passes(no_standards):
    """基线（T14.4 语义）：DI 复算通过无 error；standard 待补＋几何未联动 →
    overall=incomplete（应检未检不得伪装通过，审查验收 14）——CSR 只含已判定项
    仍 100%，coverage 只数自动可检项（待补/未联动不进分母，以 unverified 呈现）。"""
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    assert "error" not in [i["severity"] for i in ir4["issues"]]
    assert ir4["overall"] == "incomplete"
    assert ir4["csr"] == 100.0
    # coverage 只数自动可检项（formula 复算 1 项，已判定）；standard 待补/几何未联动
    # 走 unverified issue + overall 表达，不进 coverage 分母（口径注释见 agent）
    assert ir4["coverage"] == {"checked": 1, "applicable": 1, "ratio": 100.0}


def test_full_green_report_is_pass(no_standards):
    """全部必检项已判定且无未验证 → 才是 pass（几何实测联动+判定通过）。"""
    param = _param("P-001", "湿端段直径", 54, "case", "K-1", "案例登记 d=54mm", unit="mm")
    ir3 = {
        "cadquery_script": "import cadquery as cq",
        "step_file": {"bucket": "step-models", "object_key": "s/m.step"},
        "features": [{"feature_id": "F-001", "segment": "SEG-01",
                      "geometry": {"bbox_mm": [54.5, 54.5, 480.0], "volume_mm3": 1.0,
                                   "center_mm": [0, 0, 0], "linked_dim": 0},
                      "linked_param_id": "P-001"}],
        "geometry_summary": {"source": "extracted", "bbox_mm": [54.5, 54.5, 480.0],
                             "volume_mm3": 1.0, "part_count": 1},
    }
    ir4 = A4Agent().run(_ir2([param]), ir3)
    assert ir4["overall"] == "pass"
    assert ir4["csr"] == 100.0
    assert ir4["coverage"] == {"checked": 1, "applicable": 1, "ratio": 100.0}


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


def test_formula_unresolvable_source_ref_is_unverified(no_standards):
    """无法定位注册公式的 formula 参数：unverified 应检未检（T14.4 升级，原 info）——
    不硬失败（注册表会增长），但驱动 overall=incomplete，不得以"通过"收场。"""
    params = _good_params()
    params[2]["source_ref"] = "自定义手册#某公式"
    params[2]["name"] = "自定义指标"  # 避开 kb_formulas 现网行名（环境耦合：名称精确匹配会命中真库公式）
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert _severity(ir4["issues"], "CHK-FORMULA-RECOMPUTE") == ["unverified"]
    assert ir4["overall"] == "incomplete"
    assert ir4["coverage"] == {"checked": 0, "applicable": 1, "ratio": 0.0}


def test_formula_non_numeric_value_is_error():
    params = _good_params()
    params[2]["value"] = "待定"
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert _severity(ir4["issues"], "CHK-FORMULA-RECOMPUTE") == ["error"]


# ----------------------------------------------------------------------
# case 分流
# ----------------------------------------------------------------------

def test_case_interval_violation_is_warning(no_standards):
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
    # 复算自洽所以无 error；几何未联动 → incomplete（T14.4 应检未检）
    assert "error" not in [i["severity"] for i in ir4["issues"]]
    assert ir4["overall"] == "incomplete"


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

def test_standard_empty_registry_reports_pending_unverified(monkeypatch):
    """空军标库：standard 来源参数的核对义务无法履行 → unverified（T14.4 升级，
    原 info）——军标待补是应检未检，不是低风险提示。"""
    from solver import standards

    # 空库视图（T9.3 后库内有 demo 条目，空库语义需隔离验证）
    monkeypatch.setattr(standards, "load_enabled_standards", lambda max_age_s=30: [])
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    issue = _first(ir4["issues"], "CHK-STD-PENDING")
    assert issue["severity"] == "unverified"
    assert "待补" in issue["message"]


def test_case_source_param_also_checked_by_standard(monkeypatch):
    """T14.4 来源解耦（审查验收 13）：case 来源参数同样接受适用军标条目核对。"""
    from solver import standards

    spec = standards.StandardSpec(code="DEMO-C", name="间距下限", applies_to="阵元间距",
                                  expr="value >= 0.5")
    monkeypatch.setattr(standards, "REGISTRY_STANDARDS", [spec])
    monkeypatch.setattr(standards, "load_enabled_standards", lambda max_age_s=30: [])
    params = [_param("P-002", "阵元间距", 0.2, "case", "KB 案例", "案例登记 0.2m", unit="m")]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    # case 来源参数经军标条目判定通过（value 长度族换算 mm=200 ≥ 0.5）：
    # 若仍按 source_type=standard 分流，此处 csr 将为 None（无任何确定性检查）
    assert ir4["csr"] == 100.0
    assert ir4["coverage"] == {"checked": 1, "applicable": 1, "ratio": 100.0}


def test_estimated_summary_is_info_with_all_params():
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    issue = _first(ir4["issues"], "CHK-EST-UNVERIFIED")
    assert issue["severity"] == "info"
    assert issue["related_params"] == ["P-001"]


def test_info_only_and_unverified_report_is_incomplete(no_standards):
    """T14.4：unverified 存在 → incomplete；info/unverified 级问题不允许携带回环
    建议（suggestion 仅 error 级）。"""
    ir4 = A4Agent().run(_ir2(_good_params()), _ir3())
    assert ir4["overall"] == "incomplete"
    assert all("suggestion" not in i
               for i in ir4["issues"] if i["severity"] in ("info", "unverified"))


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
    """T7.4 后：空 IR3 如实提示数值复核未执行；T14.4 升级为 unverified →
    overall=incomplete；无必检项 coverage=None、无有效检查 csr=None（A-16）。"""
    ir4 = A4Agent().run(_ir2([]), _ir3())
    assert ir4["overall"] == "incomplete"
    assert [(i["constraint_id"], i["severity"]) for i in ir4["issues"]] == [("CHK-GEO-NUM-SKIP", "unverified")]
    assert ir4["csr"] is None
    assert ir4["coverage"] is None
