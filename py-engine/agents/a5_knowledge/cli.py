"""A5 独立调试入口（宪法 §9.1）。

用法（py-engine 目录下）：
    python -m agents.a5_knowledge.cli ir1.json ir2.json ir4.json
    python -m agents.a5_knowledge.cli            # 无参时用内置样例
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from agents.a4_validation.cli import _sample_ir2, _sample_ir3
from agents.a4_validation.agent import A4Agent
from agents.a5_knowledge.agent import A5Agent
from ir_contract import validate_ir
from knowledge.service import get_knowledge_service
from llm.adapter import llm_adapter
from storage.neo4j_store import Neo4jStore


def _sample_ir1() -> dict:
    return validate_ir("IR1", {
        "objectives": [{"dimension": "声阵规模", "value": 16, "explicit": True}],
        "missing_items": ["频段未明确"],
        "sub_queries": ["拖曳线列阵 阵元间距", "拖曳线列阵 指向性指数", "拖曳阵 外径约束", "拖曳阵 工作深度"],
        "retrieved_context": [
            {"doc_id": "towed_array_cad_knowledge.txt", "location": "段落12", "snippet": "样例检索片段"}
        ],
    })


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:]
    if len(args) >= 3:
        ir1 = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        ir2 = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        ir4 = json.loads(Path(args[2]).read_text(encoding="utf-8"))
    else:
        print(">> 使用内置样例（A4 现算 IR4）")
        ir1 = _sample_ir1()
        ir2 = _sample_ir2()
        ir4 = A4Agent().run(ir2, _sample_ir3())

    graph = Neo4jStore()
    if graph.available():
        print(f">> Neo4j 已连通（{graph._uri}），参与图谱查重")
    else:
        print(">> Neo4j 不可达，仅 KB 文本级去重")
        graph = None

    ir5 = A5Agent(llm=llm_adapter, kb=get_knowledge_service(), graph=graph).run(ir1, ir2, ir4)
    print(json.dumps(ir5, ensure_ascii=False, indent=2))
    print(f"\n>> 候选 {len(ir5['candidates'])} 条"
          f"（new={sum(1 for c in ir5['candidates'] if c['dedup_status'] == 'new')}，"
          f"duplicate={sum(1 for c in ir5['candidates'] if c['dedup_status'] == 'duplicate')}），"
          f"writeback_status={ir5['writeback_status']}（A-10：回写须经知识审阅关人工确认）")


if __name__ == "__main__":
    main()
