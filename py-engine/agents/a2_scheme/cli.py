"""A2 独立调试入口（宪法 §9.1）。

用法（py-engine 目录下，输入为 IR1 JSON 文件或内联需求文本走完整 A1+检索）：
    python -m agents.a2_scheme.cli ir1.json
    python -m agents.a2_scheme.cli "设计一个 16 阵元拖曳线列阵，间距 1.5 米"
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from agents.a1_requirement.agent import A1Agent
from agents.a2_scheme.agent import A2Agent
from ir_contract import validate_ir
from knowledge.service import get_knowledge_service
from llm.adapter import llm_adapter


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print('用法: python -m agents.a2_scheme.cli <ir1.json | "需求文本">')
        sys.exit(1)

    arg = sys.argv[1]
    if arg.endswith(".json") and Path(arg).exists():
        ir1 = json.loads(Path(arg).read_text(encoding="utf-8"))
    else:
        raw_text = " ".join(sys.argv[1:])
        ir1 = A1Agent(llm=llm_adapter, kb_batch=get_knowledge_service().query_batch).run(
            validate_ir("IR0", {"raw_text": raw_text})
        )

    ir2 = A2Agent(llm=llm_adapter).run(ir1)
    print(json.dumps(ir2, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
