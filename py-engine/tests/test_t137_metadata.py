# -*- coding: utf-8 -*-
"""T13.7 单测：基础元数据管理（V19 meta_* 四表 + /api/v1/metadata CRUD，§5.16 + 附录 D）。

真库（固定标记行，teardown 清理；种子行为附录 D 口径不动）。覆盖：列表/新建
（必填校验+版本自动递增）/更新（启用互斥）/删除/admin 门禁。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from storage import metadata_store
from storage.tune_store import _connect

HDRS_ADMIN = {"X-User-Id": "t-137", "X-User-Roles": "admin"}
HDRS_USER = {"X-User-Id": "t-137-user", "X-User-Roles": "designer"}
MARK = "t137-"


@pytest.fixture(scope="module", autouse=True)
def env():
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM meta_sonar_types WHERE description LIKE %s", (f"%{MARK}%",))
        cur.execute("DELETE FROM meta_freq_bands WHERE name LIKE %s", (f"%{MARK}%",))
        cur.execute("DELETE FROM meta_perf_specs WHERE name LIKE %s", (f"%{MARK}%",))
        cur.execute("DELETE FROM meta_prompt_templates WHERE name LIKE %s OR remark LIKE %s",
                    (f"%{MARK}%", f"%{MARK}%"))
        conn.commit()


def test_list_seeds_from_appendix_d():
    """附录 D 种子核对（示例口径）：双维度/频段/性能规范四类均有数据。"""
    sonar = metadata_store.list_rows("sonar_types")
    freq = metadata_store.list_rows("freq_bands")
    perf = metadata_store.list_rows("perf_specs")
    assert any(r["install_form"] == "拖曳阵" for r in sonar)
    assert any("SAS" in (r["functional_system"] or "") or r["functional_system"] == "合成孔径(SAS)" for r in sonar)
    assert {r["name"] for r in freq} >= {"低频段", "中频段", "高频段", "甚高频段"}
    assert {r["name"] for r in perf} >= {"探测距离", "方位分辨率", "距离分辨率", "发射声压级"}


def test_create_requires_fields_and_unknown_table():
    with pytest.raises(ValueError, match="双维度"):
        metadata_store.create_row("sonar_types", {"install_form": "舷侧阵"})
    with pytest.raises(ValueError, match="name"):
        metadata_store.create_row("freq_bands", {"application": "x"})
    with pytest.raises(ValueError, match="未知"):
        metadata_store.list_rows("nope")


def test_prompt_template_version_and_exclusive_enable():
    v1 = metadata_store.create_row("prompt_templates", {
        "stage": "a2_scheme", "name": f"{MARK}方案提示", "template_text": "版本一", "enabled": True})
    v2 = metadata_store.create_row("prompt_templates", {
        "stage": "a2_scheme", "name": f"{MARK}方案提示", "template_text": "版本二"})
    assert v1["version"] == 1 and v2["version"] == 2 and v2["enabled"] is False
    # 启用 v2 → v1 自动停用（同键互斥）
    updated = metadata_store.update_row("prompt_templates", v2["id"], {"enabled": True})
    assert updated["enabled"] is True
    rows = {r["id"]: r for r in metadata_store.list_rows("prompt_templates")}
    assert rows[v1["id"]]["enabled"] is False and rows[v2["id"]]["enabled"] is True


def test_update_and_delete_roundtrip():
    row = metadata_store.create_row("perf_specs", {
        "name": f"{MARK}测试参数", "unit": "m", "value_min": 1, "value_max": 10, "definition": f"{MARK}定义"})
    updated = metadata_store.update_row("perf_specs", row["id"], {"value_max": 99, "enabled": False})
    assert updated["value_max"] == 99 and updated["enabled"] is False
    assert metadata_store.delete_row("perf_specs", row["id"]) is True
    assert metadata_store.delete_row("perf_specs", row["id"]) is False


def test_api_crud_and_authz():
    client = TestClient(app)
    # 非 admin 写 → 403；读放行
    assert client.post("/api/v1/metadata/freq_bands", json={"name": f"{MARK}x"},
                       headers=HDRS_USER).status_code == 403
    resp = client.get("/api/v1/metadata/sonar_types", headers=HDRS_USER)
    assert resp.status_code == 200 and len(resp.json()["rows"]) >= 1
    # admin CRUD 全链路
    created = client.post("/api/v1/metadata/perf_specs",
                          json={"name": f"{MARK}API参数", "unit": "dB", "definition": f"{MARK}API"},
                          headers=HDRS_ADMIN)
    assert created.status_code == 201, created.text
    rid = created.json()["row"]["id"]
    patched = client.patch(f"/api/v1/metadata/perf_specs/{rid}",
                           json={"fields": {"unit": "Pa"}}, headers=HDRS_ADMIN)
    assert patched.status_code == 200 and patched.json()["row"]["unit"] == "Pa"
    assert client.delete(f"/api/v1/metadata/perf_specs/{rid}", headers=HDRS_ADMIN).status_code == 200
    # 未知类别 404
    assert client.get("/api/v1/metadata/nope", headers=HDRS_USER).status_code == 404
