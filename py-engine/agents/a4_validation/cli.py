"""A4 独立调试入口（宪法 §9.1）。

用法（py-engine 目录下）：
    python -m agents.a4_validation.cli ir2.json ir3.json
    python -m agents.a4_validation.cli            # 无参时用内置样例（含四类来源 + 一处故意违例）
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from agents.a4_validation.agent import A4Agent
from ir_contract import validate_ir


def _sample_ir2() -> dict:
    """样例 IR2：formula（正确 DI）+ case（与采纳值冲突的阵元总数候选）+ standard + estimated。"""
    return validate_ir("IR2", {
        "design_document": "# 样例设计文档（A4 CLI 调试用）\n",
        "parameters": [
            {"id": "P-001", "name": "阵元总数", "value": 16, "unit": "个",
             "source_type": "estimated", "source_ref": "IR1.objectives",
             "derivation": "用户需求显式给出（IR1 目标登记）", "confidence": "high",
             "trace_id": "tr-cli00000001"},
            {"id": "P-002", "name": "阵元总数", "value": 32, "unit": "个",
             "source_type": "case", "source_ref": "KB 案例：32 阵元拖曳阵",
             "derivation": "检索知识明确给出的案例参数：32 阵元案例", "confidence": "high",
             "trace_id": "tr-cli00000002"},
            {"id": "P-003", "name": "阵元间距", "value": 1.5, "unit": "m",
             "source_type": "case", "source_ref": "KB 案例：间距 1.5m",
             "derivation": "检索知识明确给出的案例参数：间距 1.5m", "confidence": "high",
             "trace_id": "tr-cli00000003"},
            {"id": "P-004", "name": "指向性指数 DI", "value": 12.0412, "unit": "dB",
             "source_type": "formula", "source_ref":
             "towed_array_cad_knowledge.txt#声学约束公式#DI≈10lg(N)",
             "derivation": "指向性指数 DI = n=16 → 代入计算得 12.0412 dB",
             "confidence": "high", "trace_id": "tr-cli00000004"},
            {"id": "P-005", "name": "灵敏度", "value": -195, "unit": "dB",
             "source_type": "standard", "source_ref": "GJB（待入库）",
             "derivation": "军标条目待入库，暂按案例登记", "confidence": "medium",
             "trace_id": "tr-cli00000005"},
        ],
    })


def _sample_ir3() -> dict:
    return validate_ir("IR3", {
        "cadquery_script": "import cadquery as cq\n# 样例脚本\n",
        "step_file": {"bucket": "step-models", "object_key": "cli-sample/sonar_model.step"},
        "features": [
            {"feature_id": "F-001", "segment": "model",
             "geometry": {"linked_param": "P-001", "note": "阵元数驱动阵列段"},
             "linked_param_id": "P-001"},
            {"feature_id": "F-002", "segment": "model",
             "geometry": {"linked_param": "P-999", "note": "故意注入的悬空引用"},
             "linked_param_id": "P-999"},
        ],
    })


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:]
    if len(args) >= 2:
        ir2 = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        ir3 = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    else:
        print(">> 使用内置样例（故意注入：案例冲突 16vs32 / 悬空特征引用 P-999）")
        ir2, ir3 = _sample_ir2(), _sample_ir3()

    ir4 = A4Agent().run(ir2, ir3)
    print(json.dumps(ir4, ensure_ascii=False, indent=2))
    errors = [i for i in ir4["issues"] if i["severity"] == "error"]
    print(f"\n>> overall={ir4['overall']}  issues={len(ir4['issues'])}（error={len(errors)}）")


if __name__ == "__main__":
    main()
