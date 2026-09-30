"""IR 契约测试（宪法 §9.2）。

contracts/ir/ 是单一事实源（A-04）：本测试用合法/非法示例实例校验 Schema 本身的
约束力——A-06（缺来源的参数不允许进入 IR2）与 P2 附录字段在此固化。
Schema 变更必须同步更新本测试（同一 commit）。
"""

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _load(name: str) -> dict:
    with open(CONTRACTS / "ir" / name, encoding="utf-8") as f:
        return json.load(f)


def _validate(schema: dict, instance: dict) -> None:
    Draft202012Validator.check_schema(schema)  # Schema 自身必须合法
    Draft202012Validator(schema).validate(instance)


def test_all_ir_schemas_are_valid_json_schema() -> None:
    for i in range(6):
        _load(f"ir{i}.schema.json")


def test_ir0_minimal() -> None:
    _validate(_load("ir0.schema.json"), {"raw_text": "设计一型拖曳线列阵声呐"})


def test_ir1_structure() -> None:
    ir1 = {
        "objectives": [
            {"dimension": "声阵规模", "value": "64 元", "explicit": True},
            {"dimension": "工作深度", "value": None, "explicit": False},
        ],
        "missing_items": ["工作深度未给出"],
        "sub_queries": ["q1", "q2", "q3", "q4"],
        "retrieved_context": [{"doc_id": "D-01", "location": "p.12 §3", "snippet": "..."}],
    }
    _validate(_load("ir1.schema.json"), ir1)
    # 子查询必须 4~8 条
    ir1["sub_queries"] = ["only-one"]
    with pytest.raises(Exception):
        _validate(_load("ir1.schema.json"), ir1)


VALID_PARAMETER = {
    "id": "P-014",
    "name": "声阵段外径",
    "value": 27,
    "unit": "mm",
    "source_type": "case",
    "source_ref": "K-118",
    "derivation": "引用拖曳阵设计案例阈值",
    "confidence": "high",
    "trace_id": "T-0042",
}


def test_ir2_parameter_requires_source() -> None:
    schema = _load("ir2.schema.json")
    ir2 = {"design_document": "# 方案", "parameters": [VALID_PARAMETER]}
    _validate(schema, ir2)

    # A-06：缺 source_type/source_ref/derivation/trace_id 任一项均非法
    for missing in ("source_type", "source_ref", "derivation", "trace_id"):
        bad = {k: v for k, v in VALID_PARAMETER.items() if k != missing}
        with pytest.raises(Exception):
            _validate(schema, {"design_document": "# 方案", "parameters": [bad]})

    # source_type 只允许四分类
    bad = dict(VALID_PARAMETER, source_type="guessed")
    with pytest.raises(Exception):
        _validate(schema, {"design_document": "# 方案", "parameters": [bad]})


def test_ir3_feature_links_param() -> None:
    ir3 = {
        "cadquery_script": "import cadquery as cq\n...",
        "step_file": {"bucket": "step-models", "object_key": "sessions/s1/rev1.step"},
        "features": [
            {"feature_id": "F-07", "segment": "声阵段", "geometry": {"outer_dia_mm": 27}, "linked_param_id": "P-014"}
        ],
    }
    _validate(_load("ir3.schema.json"), ir3)


def test_ir3_legacy_form_still_valid_after_phase7() -> None:
    """T7.1 加法演进回归：PHASE-007 之前的旧形态（无新字段、linked 非空、geometry 自由 object）仍合法。"""
    ir3 = {
        "cadquery_script": "import cadquery as cq\n...",
        "step_file": {"bucket": "step-models", "object_key": "sessions/s1/rev1.step"},
        "features": [
            {"feature_id": "F-001", "segment": "model",
             "geometry": {"linked_param": "P-004", "note": "占位镜像（旧数据）"},
             "linked_param_id": "P-004"},
        ],
    }
    _validate(_load("ir3.schema.json"), ir3)


def test_ir3_phase7_extracted_form_valid() -> None:
    """T7.1 新形态：实测分段 + 几何数值 + linked_param_id=null 如实未联动 + 顶层 geometry_summary。"""
    ir3 = {
        "cadquery_script": "import cadquery as cq\n...",
        "step_file": {"bucket": "step-models", "object_key": "s1/sonar_model.step"},
        "features": [
            {"feature_id": "F-001", "segment": "SEG-01",
             "geometry": {"bbox_mm": [54.0, 54.0, 480.0], "volume_mm3": 1099320.5,
                          "center_mm": [0.0, 0.0, 240.0], "linked_dim": 2},
             "linked_param_id": "P-004"},
            {"feature_id": "F-002", "segment": "SEG-02",
             "geometry": {"bbox_mm": [40.0, 40.0, 1200.0], "volume_mm3": 1507964.4,
                          "center_mm": [0.0, 0.0, 3720.0]},
             "linked_param_id": None},
        ],
        "geometry_summary": {
            "source": "extracted",
            "bbox_mm": [54.0, 54.0, 4800.0],
            "volume_mm3": 4215000.0,
            "part_count": 2,
        },
    }
    _validate(_load("ir3.schema.json"), ir3)


def test_ir3_phase7_extraction_failed_summary_valid() -> None:
    """T7.1 降级形态：提取失败如实无数值（不造假 0，A-16）。"""
    ir3 = {
        "cadquery_script": "import cadquery as cq\n...",
        "step_file": {"bucket": "step-models", "object_key": "s1/sonar_model.step"},
        "features": [],
        "geometry_summary": {"source": "extraction_failed", "detail": "subprocess timeout after 60s"},
    }
    _validate(_load("ir3.schema.json"), ir3)
    # 旧快照（无 geometry_summary）依然合法
    del ir3["geometry_summary"]
    _validate(_load("ir3.schema.json"), ir3)


def test_ir3_phase7_new_fields_constraints() -> None:
    schema = _load("ir3.schema.json")
    base = {
        "cadquery_script": "import cadquery as cq\n...",
        "step_file": {"bucket": "step-models", "object_key": "s1/sonar_model.step"},
        "features": [],
    }
    # source 枚举外的值非法
    bad = dict(base, geometry_summary={"source": "guessed"})
    with pytest.raises(Exception):
        _validate(schema, bad)
    # geometry_summary 未知名拒绝（additionalProperties: false）
    bad = dict(base, geometry_summary={"source": "extracted", "unknown": 1})
    with pytest.raises(Exception):
        _validate(schema, bad)
    # bbox_mm 长度必须为 3
    bad = dict(base, geometry_summary={"source": "extracted", "bbox_mm": [1.0, 2.0]})
    with pytest.raises(Exception):
        _validate(schema, bad)
    # linked_param_id 缺失仍非法（required 保留，只是放宽为可 null）
    bad = dict(base, features=[{"feature_id": "F-001", "segment": "SEG-01", "geometry": {}}])
    with pytest.raises(Exception):
        _validate(schema, bad)


def test_ir4_issue_suggestion_only_local_back() -> None:
    schema = _load("ir4.schema.json")
    ir4 = {
        "overall": "fail",
        "issues": [
            {"constraint_id": "C-001", "severity": "error",
             "related_params": ["P-014"], "related_features": ["F-07"],
             "suggestion": "back_to_A2"}
        ],
    }
    _validate(schema, ir4)
    # 回退建议只允许局部回环目标（A-11）
    bad = json.loads(json.dumps(ir4))
    bad["issues"][0]["suggestion"] = "rerun_all"
    with pytest.raises(Exception):
        _validate(schema, bad)


def test_ir4_three_level_severity_and_optional_suggestion() -> None:
    """T2.1 契约微扩：severity 增加 info（三级风险）；info 级可不带 suggestion。"""
    schema = _load("ir4.schema.json")
    ir4 = {
        "overall": "pass",
        "issues": [
            {"constraint_id": "CHK-EST-001", "severity": "info",
             "related_params": ["P-003"],
             "related_features": [],
             "message": "2 个估算参数未经公式验证，请人工复核"},
            {"constraint_id": "CHK-CASE-001", "severity": "warning",
             "related_params": ["P-005", "P-009"], "related_features": [],
             "message": "阵元总数案例候选冲突：16 vs 32", "suggestion": "back_to_A2"},
        ],
    }
    _validate(schema, ir4)
    # severity 只允许三级，无第四级
    bad = {"overall": "pass", "issues": [{"constraint_id": "X", "severity": "critical",
                                          "related_params": [], "related_features": []}]}
    with pytest.raises(Exception):
        _validate(schema, bad)


def test_ir4_csr_field_additive_evolution() -> None:
    """T9.2 契约加法演进：可空 csr（0–100 两位小数）；旧快照（无 csr）双形态兼容。"""
    schema = _load("ir4.schema.json")
    # 旧形态（历史快照）：无 csr 字段仍合法
    old_form = {"overall": "pass", "issues": []}
    _validate(schema, old_form)
    # 新形态：数值
    _validate(schema, {"overall": "pass", "issues": [], "csr": 87.5})
    # 新形态：null（无有效检查，A-16 不造假）
    _validate(schema, {"overall": "pass", "issues": [], "csr": None})
    # 越界 / 类型错误非法
    with pytest.raises(Exception):
        _validate(schema, {"overall": "pass", "issues": [], "csr": 150})
    with pytest.raises(Exception):
        _validate(schema, {"overall": "pass", "issues": [], "csr": "95"})


def test_ir5_writeback_pending_by_default() -> None:
    schema = _load("ir5.schema.json")
    ir5 = {
        "candidates": [{"type": "case", "payload": {"name": "拖曳阵案例"}, "dedup_status": "new"}],
        "writeback_status": "pending",
    }
    _validate(schema, ir5)
    # 非法状态值
    with pytest.raises(Exception):
        _validate(schema, {"candidates": [], "writeback_status": "auto_written"})


def test_ws_message_schema() -> None:
    with open(CONTRACTS / "ws" / "ws-message.schema.json", encoding="utf-8") as f:
        schema = json.load(f)
    msg = {
        "type": "task_progress",
        "session_id": "s-001",
        "task_id": "t-001",
        "timestamp": "2026-09-17T08:00:00Z",
        "payload": {"stage": "A3", "status": "running", "progress": 40},
    }
    _validate(schema, msg)
    # 裸字符串载荷不允许（宪法 §5.5）
    with pytest.raises(Exception):
        _validate(schema, {"type": "task_progress", "session_id": "s", "timestamp": "2026-09-17T08:00:00Z",
                           "payload": "running A3"})
