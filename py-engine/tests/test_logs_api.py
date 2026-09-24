# -*- coding: utf-8 -*-
"""会话运行日志留存单测：log_store 落库/回放 + /api/v1/logs 查询（V11，总览「全部日志」）。

真库（固定 uuid 段 …500000000001，teardown 清理）。worker _publish_log 的落库调用
在 test_t82 语义覆盖之外，此处直接驱动 log_store（同一入口）。
"""

from __future__ import annotations

import uuid as uuid_mod

import pytest
from fastapi.testclient import TestClient

from api.main import app
from storage import log_store
from storage.tune_store import _connect

DESIGN_SID = "00000000-0000-0000-0000-500000000001"
HDRS = {"X-User-Id": "t-logs", "X-User-Roles": "admin"}


@pytest.fixture(scope="module", autouse=True)
def env():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO design_sessions (id, project_id, pipeline_status, title)
               SELECT %s::uuid, p.id, 'completed', 't-logs-日志留存单测' FROM projects p LIMIT 1
               ON CONFLICT (id) DO NOTHING""",
            (DESIGN_SID,),
        )
        cur.execute("DELETE FROM session_logs WHERE session_id = %s", (DESIGN_SID,))
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM session_logs WHERE session_id = %s", (DESIGN_SID,))
        cur.execute("DELETE FROM design_sessions WHERE id = %s", (DESIGN_SID,))


def test_append_and_list_ascending(env):
    log_store.append(DESIGN_SID, "info", "A1 开始")
    log_store.append(DESIGN_SID, "ok", "A1 完成")
    rows = log_store.list_logs(DESIGN_SID)
    assert [r["text"] for r in rows] == ["A1 开始", "A1 完成"]
    assert rows[0]["level"] == "info" and rows[1]["level"] == "ok"
    assert isinstance(rows[0]["id"], int) and isinstance(rows[0]["created_at"], str)


def test_list_after_id_incremental(env):
    rows = log_store.list_logs(DESIGN_SID)
    assert len(rows) >= 2
    rest = log_store.list_logs(DESIGN_SID, after_id=rows[0]["id"])
    assert [r["id"] for r in rest] == [r["id"] for r in rows][1:]


def test_unknown_session_returns_empty(env):
    assert log_store.list_logs(str(uuid_mod.uuid4())) == []


def test_api_get_session_logs(env):
    client = TestClient(app)
    resp = client.get(f"/api/v1/logs/sessions/{DESIGN_SID}", headers=HDRS)
    assert resp.status_code == 200, resp.text
    logs = resp.json()["logs"]
    assert [r["text"] for r in logs] == ["A1 开始", "A1 完成"]
    # 未登录（无信任头/无 JWT）应被拒
    assert client.get(f"/api/v1/logs/sessions/{DESIGN_SID}").status_code in (401, 403)
