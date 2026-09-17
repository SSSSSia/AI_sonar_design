"""A1 独立调试入口（宪法 §9.1：Agent 可脱离管线单独运行）。

用法（在 py-engine 目录下）：
    python -m agents.a1_requirement.cli "设计一个 8 阵元拖曳线列阵声呐，工作深度 300 米"
"""

from __future__ import annotations

import json
import logging
import sys

from agents.a1_requirement.agent import A1Agent
from ir_contract import SCHEMA_VERSION, validate_ir
from knowledge.service import get_knowledge_service
from llm.adapter import llm_adapter


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m agents.a1_requirement.cli \"<需求文本>\"")
        sys.exit(1)
    raw_text = " ".join(sys.argv[1:])

    ir0 = validate_ir("IR0", {"raw_text": raw_text})
    agent = A1Agent(
        llm=llm_adapter,
        kb_batch=get_knowledge_service().query_batch,
    )
    ir1 = agent.run(ir0)

    print(f"# schema_version: {SCHEMA_VERSION}")
    print(json.dumps(ir1, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
