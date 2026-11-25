# -*- coding: utf-8 -*-
"""T17.4 存量范例向量回填（ADR-016 §4；PHASE-017）。

对 ref_scripts 全部 active 行 download→embed→upsert（doc_id=ref_id 先删后插，幂等）。
诚实计数报告 synced/skipped_empty/failed（A-16 不伪造成功）。重复执行幂等（同 ref_id
覆盖）。停用/历史版本行不嵌（active-only）。

用法（仓库根，需 .env 且 llm_providers 有 enabled 真实供应商）：
    set -a; source .env; set +a
    python scripts/backfill_ref_vectors.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from storage import object_store, ref_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T17.4 存量范例向量回填（幂等，诚实计数）")
    args = ap.parse_args(argv)

    rows = ref_store.get_active_rows()
    if not rows:
        print("无 active 范例行——如实退出（未做任何写入）")
        return 0
    from cad_engine.ref_vector import sync_ref_vector

    synced = skipped_empty = failed = 0
    for row in rows:
        try:
            content = object_store.download_text(row["bucket"], row["object_key"])
        except Exception as exc:
            print(f"  download 失败 {row['ref_id']} v{row['version']}: {exc}")
            failed += 1
            continue
        if not content:
            skipped_empty += 1
            continue
        if sync_ref_vector(row, content):
            synced += 1
        else:
            failed += 1
    print(json.dumps({"active_rows": len(rows), "synced": synced,
                      "skipped_empty": skipped_empty, "failed": failed}, ensure_ascii=False))
    if failed:
        print("存在失败（供应商/对象读取侧）——重跑本脚本可续补（幂等）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
