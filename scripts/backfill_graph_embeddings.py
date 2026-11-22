# -*- coding: utf-8 -*-
"""T17.3 存量实体 embedding 回填（ADR-016 §3；PHASE-017）。

对 live Neo4j 全量 key 节点挂 KnowledgeEntity 公共标签 + 非 deprecated 缺失者批量嵌入
（bge-m3 1024 维，与 kb_chunks 同链 embed_texts）。幂等（已嵌跳过；--force 重算）。
诚实报告 embedded/skipped_empty/failed（A-16 不伪造成功）。

用法（仓库根，需 .env 且 llm_providers 有 enabled 真实供应商）：
    set -a; source .env; set +a
    python scripts/backfill_graph_embeddings.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from storage.neo4j_store import Neo4jStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T17.3 存量实体 embedding 回填（诚实报告，幂等）")
    ap.add_argument("--force", action="store_true", help="已嵌入者也重算（默认跳过）")
    args = ap.parse_args(argv)

    store = Neo4jStore()
    if not store.available():
        print("Neo4j 不可达——如实退出（未做任何写入）")
        return 2
    report = store.backfill_entity_embeddings(force=args.force)
    print(json.dumps(report, ensure_ascii=False))
    if report.get("failed"):
        print("存在失败批次（供应商侧）——重跑本脚本可续补（幂等）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
