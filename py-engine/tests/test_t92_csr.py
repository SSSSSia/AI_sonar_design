# -*- coding: utf-8 -*-
"""T9.2 CSR 约束满足率单测（PHASE-009 §2.2 口径）。

口径：CSR = 通过 / (通过 + 违反) × 100。
- 违反 = error 级的确定性检查（formula 复算不符 / 军标违例 / 几何数值超容差）；
- warning（存疑）与 info（提示/待补/跳过）不计入分母；
- 全 skip / 空方案 → csr=None（A-16 无数据不造假）。
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.a4_validation.agent import A4Agent

KB_REF = "towed_array_cad_knowledge.txt#声学约束公式"


def _param(pid, name, value, source_type, source_ref, derivation, unit="-", confidence="high"):
    return {
        "id": pid, "name": name, "value": value, "unit": unit,
        "source_type": source_type, "source_ref": source_ref,
        "derivation": derivation, "confidence": confidence,
        "trace_id": f"tr-{pid.lower()}",
    }


def _ir2(params):
    return {"design_document": "# 样例\n", "parameters": params}


def _ir3(features=None, extracted=True):
    return {
        "cadquery_script": "import cadquery as cq\n",
        "step_file": {"bucket": "step-models", "object_key": "s/sonar_model.step"},
        "features": features or [],
        **({"geometry_summary": {"source": "extracted", "part_count": 1, "volume_mm3": 1.0e6}}
           if extracted else {}),
    }


def _di_param(pid, n, tampered=False):
    """REGISTRY 公式 DI≈10lg(N)：n 阵元 → 10·lg(n) dB。"""
    exact = round(10.0 * math.log10(n), 4)
    value = exact + 5.0 if tampered else exact
    return _param(pid, f"指向性指数{pid}", value, "formula", f"{KB_REF}#DI≈10lg(N)",
                  f"指向性指数 DI = n={n} → 代入计算得 {value} dB", unit="dB")


def test_csr_all_pass_is_100():
    ir4 = A4Agent().run(_ir2([_di_param("P-001", 16)]), _ir3())
    assert ir4["csr"] == 100.0


def test_csr_mixed_pass_and_violation():
    """1 个公式通过 + 1 个公式篡改（error）→ 50.00；估算/案例/军标待补均不进分母。"""
    params = [
        _di_param("P-001", 16),                       # 通过
        _di_param("P-002", 32, tampered=True),        # 违反（复算不符 error）
        _param("P-003", "阵元间距", 1.5, "case", "KB 案例：间距 1.5m",
               "检索知识明确给出的案例参数：间距 1.5m", unit="m"),
        _param("P-004", "灵敏度", -195, "standard", "GJB（待入库）",
               "军标条目待入库，暂按案例登记", unit="dB", confidence="medium"),
        _param("P-005", "质量目标", 50, "estimated", "IR1.objectives",
               "用户需求显式给出"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert ir4["csr"] == 50.0, "通过 1 + 违反 1 → 50.00（warning/info/待补不计入分母）"
    assert ir4["overall"] == "fail"


def test_csr_null_when_no_effective_checks():
    """全 skip（无公式参数 + 几何无数值复核）→ csr=None，不显示 0%/100%（A-16）。"""
    params = [
        _param("P-001", "阵元间距", 1.5, "case", "KB 案例：间距 1.5m",
               "检索知识明确给出的案例参数：间距 1.5m", unit="m"),
        _param("P-002", "灵敏度", -195, "standard", "GJB（待入库）",
               "军标条目待入库，暂按案例登记", unit="dB", confidence="medium"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())  # 无联动特征 → 几何数值复核 skip
    assert ir4["csr"] is None


def test_csr_empty_params_is_null():
    ir4 = A4Agent().run(_ir2([]), _ir3())
    assert ir4["csr"] is None


def test_csr_geometry_numeric_counts():
    """CHK-GEO-NUM：容差内计通过、超容差错误计违反——1 通过 + 1 违反 → 50.00。"""
    params = [
        _param("P-010", "阵段长度A", 100.0, "case", "KB 案例", "案例登记", unit="mm"),
        _param("P-011", "阵段长度B", 50.0, "case", "KB 案例", "案例登记", unit="mm"),
    ]
    features = [
        {"feature_id": "F-001", "segment": "SEG-01", "linked_param_id": "P-010",
         "geometry": {"bbox_mm": [100.0, 60.0, 30.0], "linked_dim": 0}},   # 实测 100 → 一致
        {"feature_id": "F-002", "segment": "SEG-02", "linked_param_id": "P-011",
         "geometry": {"bbox_mm": [100.0, 61.0, 30.0], "linked_dim": 1}},   # 实测 61 vs 50 → 超 2%
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3(features=features))
    assert ir4["csr"] == 50.0, "几何数值复核 1 通过 + 1 违反 → 50.00"
    assert any(i["constraint_id"] == "CHK-GEO-NUM" and i["severity"] == "error"
               for i in ir4["issues"])


def test_csr_warning_does_not_dilute():
    """case 冲突 warning（存疑）不进分母：只有 1 个公式通过时 CSR 仍 100。"""
    params = [
        _di_param("P-001", 16),
        _param("P-002", "阵元间距", 1.5, "case", "KB 案例：间距 1.5m",
               "检索知识明确给出的案例参数：间距 1.5m", unit="m"),
        _param("P-003", "阵元间距", 2.0, "case", "KB 案例：间距 2.0m",
               "检索知识明确给出的案例参数：间距 2.0m", unit="m"),
    ]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    has_warn = any(i["severity"] == "warning" for i in ir4["issues"])
    assert ir4["csr"] == 100.0, f"warning 不计入分母（has_warn={has_warn}）"


def test_csr_two_decimals():
    """1 通过 + 2 违反 → 33.33（两位小数）。"""
    params = [_di_param("P-001", 16),
              _di_param("P-002", 32, tampered=True),
              _di_param("P-003", 64, tampered=True)]
    ir4 = A4Agent().run(_ir2(params), _ir3())
    assert ir4["csr"] == 33.33
