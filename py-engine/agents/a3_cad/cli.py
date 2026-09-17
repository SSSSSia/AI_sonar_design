"""A3 独立调试入口（宪法 §9.1）。

用法（py-engine 目录下）：
    python -m agents.a3_cad.cli ir2.json            # 从 IR2 JSON 生成并出 STEP（落 MinIO cli-debug/）
    python -m agents.a3_cad.cli --selftest          # 内置最小 IR2 冒烟（mock 脚本，验证沙箱+MinIO 链路）
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from agents.a3_cad.agent import A3Agent
from ir_contract import validate_ir
from llm.adapter import llm_adapter

SELFTEST_IR2 = {
    "design_document": "# 最小冒烟件\n\n| 序号 | 部件名称 | 长度(mm) | 外径(mm) |\n| 1 | 圆柱 | 100 | 27 |",
    "parameters": [
        {"id": "P-001", "name": "外径", "value": 27, "unit": "mm", "source_type": "formula",
         "source_ref": "selftest", "derivation": "selftest", "confidence": "high", "trace_id": "tr-selftest"}
    ],
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m agents.a3_cad.cli <ir2.json | --selftest>")
        sys.exit(1)

    if sys.argv[1] == "--selftest":
        ir2 = validate_ir("IR2", SELFTEST_IR2)
    else:
        ir2 = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    ir3 = A3Agent(llm=llm_adapter).run("cli-debug", ir2)
    print(json.dumps({k: (v if k != "cadquery_script" else f"<{len(v)} 字符脚本>") for k, v in ir3.items()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
