"""溯源链写入（T2.4，表 traceability_chain 由平台 V1 建表）。

写入主体：worker 落 IR2/IR3/IR4/IR5 快照时同步记录参数/特征/问题/候选级链路步骤；
审阅裁决由 Java 侧写入 confirm 行。链路构造（trace_steps.py）为纯函数，可独立单测。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from storage.ir_store import _connect

logger = logging.getLogger("sonarcad.trace")


def record_steps(rows: list[dict[str, Any]]) -> int:
    """批量写入链路步骤，返回写入条数。rows 元素：
    {trace_id, step_no, actor, action, ir_snapshot_id, payload}

    事务内批量插入，任一条失败整体回滚并上抛。worker 调用点（tasks._record_trace）
    降级为记日志后继续：IR 快照（含四件套）是权威出处，链路可由快照重建，
    trace API 无行即 404 如实（A-16）。
    """
    if not rows:
        return 0
    sql = (
        "INSERT INTO traceability_chain "
        "(trace_id, step_no, session_id, actor, action, ir_snapshot_id, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, [
                (
                    r["trace_id"], r["step_no"], r.get("session_id"), r["actor"],
                    r["action"], r.get("ir_snapshot_id"),
                    json.dumps(r.get("payload", {}), ensure_ascii=False),
                )
                for r in rows
            ])
        conn.commit()
    logger.info("溯源链写入 %d 条", len(rows))
    return len(rows)
