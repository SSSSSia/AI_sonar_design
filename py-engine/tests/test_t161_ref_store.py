# -*- coding: utf-8 -*-
"""T16.1 B1 范例库最小管理单测（PHASE-016 §3.5 步骤 1，契约=ADR-015）。

真库（tests 与现网共用 sonarcad 库）：ref_scripts 表（biz V21），测试行用 zztest_t161
前缀 ref_id + teardown 删除，不碰现网数据。覆盖：
- store 层：CRUD/版本序列/导入幂等判定/单活互斥（confirm 与重启用两条路）/状态机校验
- API 层：TestClient + X-User 头（test_t83 模式）——上传登记/pending 须 confirm/导入对照报告/RBAC
（MinIO 上传一律 monkeypatch 记录调用，不打真实对象存储；哈希口径 sha256(文本 utf-8) 断言）。
"""

from __future__ import annotations

import hashlib
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from storage import ref_store
from storage.ref_store import _connect

REF_PREFIX = "zztest_t161_"
SCRIPT_V1 = "# -*- coding: utf-8 -*-\n# t161 测试范例 v1\nprint('v1')\n"
SCRIPT_V2 = "# -*- coding: utf-8 -*-\n# t161 测试范例 v2（内容变更）\nprint('v2')\n"


def _sha(text: str) -> str:
    """哈希口径同源断言基准：sha256(脚本文本 utf-8)——与 cad_engine/reference.py:111 一致。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_rows():
    yield
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ref_scripts WHERE ref_id LIKE %s", (REF_PREFIX + "%",))


def _insert(rid_suffix: str, version: int, *, content: str = SCRIPT_V1, status: str = "pending",
            source_type: str = "manual_upload") -> dict:
    return ref_store.insert_row(
        ref_id=REF_PREFIX + rid_suffix, version=version, file_name=f"t161_{rid_suffix}_v{version}.py",
        title="t161 单测", description=None, domain="towed_array", domain_rules="towed_array_v1",
        sonar_types=["towed_array"], keywords=["拖曳阵"], bucket="ref-scripts",
        object_key=f"{REF_PREFIX + rid_suffix}/v{version}/t161.py", content_sha256=_sha(content),
        status=status, source_type=source_type, source_note="单测", created_by="t161",
    )


# ----------------------------------------------------------------------
# store 层
# ----------------------------------------------------------------------
def test_insert_and_roundtrip():
    row = _insert("roundtrip", 1)
    got = ref_store.get_row(row["id"])
    assert got is not None
    assert got["ref_id"] == REF_PREFIX + "roundtrip"
    assert got["sonar_types"] == ["towed_array"]  # jsonb 出参反序列化为 list
    assert got["keywords"] == ["拖曳阵"]
    assert got["status"] == "pending"
    assert isinstance(got["id"], str) and re.match(r"^[0-9a-f-]{36}$", got["id"])
    assert "T" in got["created_at"] and "T" in got["updated_at"]  # isoformat 出口（T6.7 教训）
    rows = ref_store.list_rows(REF_PREFIX + "roundtrip")
    assert [r["version"] for r in rows] == [1]


def test_next_version_sequence():
    rid = REF_PREFIX + "seq"
    assert ref_store.next_version(rid) == 1
    _insert("seq", 1)
    assert ref_store.next_version(rid) == 2


def test_find_by_content_idempotent():
    row = _insert("idem", 1, content=SCRIPT_V1)
    hit = ref_store.find_by_content(REF_PREFIX + "idem", _sha(SCRIPT_V1))
    assert hit is not None and hit["id"] == row["id"]
    assert ref_store.find_by_content(REF_PREFIX + "idem", _sha(SCRIPT_V2)) is None
    assert ref_store.find_by_content(REF_PREFIX + "other", _sha(SCRIPT_V1)) is None


def test_unique_ref_id_version():
    _insert("uniq", 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert("uniq", 1)


def test_insert_validations():
    with pytest.raises(ValueError):
        _insert("badstatus", 1, status="running")
    with pytest.raises(ValueError):
        ref_store.insert_row(
            ref_id=REF_PREFIX + "badsrc", version=1, file_name="x.py", title=None, description=None,
            domain=None, domain_rules=None, sonar_types=[], keywords=[], bucket="ref-scripts",
            object_key="x", content_sha256=_sha(SCRIPT_V1), status="pending",
            source_type="unknown_src", source_note=None, created_by=None,
        )


def test_confirm_single_active():
    v1 = _insert("single", 1, content=SCRIPT_V1)
    confirmed = ref_store.confirm_row(v1["id"])
    assert confirmed["status"] == "active"
    v2 = _insert("single", 2, content=SCRIPT_V2)
    ref_store.confirm_row(v2["id"])
    rows = {r["version"]: r for r in ref_store.list_rows(REF_PREFIX + "single")}
    assert rows[1]["status"] == "disabled"   # 单活互斥：旧 active 自动停用
    assert rows[2]["status"] == "active"
    active = ref_store.get_active_rows()
    assert [r["version"] for r in active if r["ref_id"] == REF_PREFIX + "single"] == [2]


def test_confirm_requires_pending():
    row = _insert("confonly", 1)
    ref_store.confirm_row(row["id"])  # pending → active
    with pytest.raises(ValueError):
        ref_store.confirm_row(row["id"])  # active 再确认 → 409


def test_reenable_via_update_meta_single_active():
    v1 = _insert("reenable", 1)
    ref_store.confirm_row(v1["id"])
    v2 = _insert("reenable", 2, content=SCRIPT_V2)
    ref_store.confirm_row(v2["id"])
    # 重新启用旧版本（回退场景）：单活互斥生效
    back = ref_store.update_meta(v1["id"], {"status": "active"})
    assert back["status"] == "active"
    rows = {r["version"]: r for r in ref_store.list_rows(REF_PREFIX + "reenable")}
    assert rows[2]["status"] == "disabled"
    # pending → active 走 update_meta 禁止（须 confirm）
    v3 = _insert("reenable", 3)
    with pytest.raises(ValueError):
        ref_store.update_meta(v3["id"], {"status": "active"})


def test_update_meta_whitelist_and_fields():
    row = _insert("meta", 1)
    got = ref_store.update_meta(row["id"], {"title": "改名", "description": "新描述",
                                            "sonar_types": ["a", "b"], "ref_id": "hack"})
    assert got["title"] == "改名" and got["description"] == "新描述"
    assert got["sonar_types"] == ["a", "b"]
    assert got["ref_id"] == REF_PREFIX + "meta"  # 白名单外字段不可写
    with pytest.raises(ValueError):
        ref_store.update_meta(row["id"], {"ref_id": "hack"})  # 纯白名单外 → 无可写字段
    with pytest.raises(ValueError):
        ref_store.update_meta(row["id"], {"status": "running"})


def test_snapshot_fingerprint():
    row = _insert("fingerprint", 1)
    fp = ref_store.snapshot_fingerprint()
    mine = [f for f in fp if f[0] == REF_PREFIX + "fingerprint"]
    assert mine and mine[0][1] == 1 and mine[0][2] == "pending" and "T" in mine[0][3]


# ----------------------------------------------------------------------
# API 层（TestClient + X-User 头；MinIO monkeypatch）
# ----------------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    uploaded: list[tuple[str, str]] = []

    def fake_upload_bytes(data: bytes, bucket: str, object_key: str, content_type: str = "") -> dict:
        uploaded.append((bucket, object_key))
        return {"bucket": bucket, "object_key": object_key, "sha256": hashlib.sha256(data).hexdigest()}

    def fake_upload_text(text: str, bucket: str, object_key: str) -> dict:
        uploaded.append((bucket, object_key))
        return {"bucket": bucket, "object_key": object_key, "sha256": _sha(text)}

    monkeypatch.setattr("storage.object_store.upload_bytes", fake_upload_bytes)
    monkeypatch.setattr("storage.object_store.upload_text", fake_upload_text)
    from api.main import app

    with TestClient(app) as c:
        c.headers.update({"X-User-Id": "t161-tester", "X-User-Roles": "admin"})
        c.uploaded = uploaded  # type: ignore[attr-defined]
        yield c


def test_api_upload_pending_then_confirm(client):
    resp = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_api_demo.py", SCRIPT_V1.encode("utf-8"), "text/x-python")},
        data={"ref_id": REF_PREFIX + "api", "domain": "towed_array", "domain_rules": "towed_array_v1",
              "sonar_types": ["towed_array"], "keywords": ["拖曳阵", "阵列"]},
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["row"]
    assert row["status"] == "pending" and row["version"] == 1
    assert row["content_sha256"] == _sha(SCRIPT_V1)
    assert row["sonar_types"] == ["towed_array"] and row["keywords"] == ["拖曳阵", "阵列"]
    assert client.uploaded and client.uploaded[0][0] == "ref-scripts"  # 桶正确

    # pending 激活必须走 confirm
    r409 = client.patch(f"/api/v1/kb/references/{row['id']}", json={"status": "active"})
    assert r409.status_code == 409
    # 停用 pending（作废）允许
    r_dis = client.patch(f"/api/v1/kb/references/{row['id']}", json={"status": "disabled"})
    assert r_dis.status_code == 200 and r_dis.json()["row"]["status"] == "disabled"
    # 确认路径：重新走一遍新版本
    resp2 = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_api_demo2.py", SCRIPT_V2.encode("utf-8"), "text/x-python")},
        data={"ref_id": REF_PREFIX + "api"},
    )
    assert resp2.status_code == 200 and resp2.json()["row"]["version"] == 2
    r_ok = client.post(f"/api/v1/kb/references/{resp2.json()['row']['id']}/confirm")
    assert r_ok.status_code == 200 and r_ok.json()["row"]["status"] == "active"


def test_api_upload_duplicate_content_409(client):
    resp1 = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_dup.py", SCRIPT_V1.encode("utf-8"), "text/x-python")},
        data={"ref_id": REF_PREFIX + "dup"},
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_dup_other_name.py", SCRIPT_V1.encode("utf-8"), "text/x-python")},
        data={"ref_id": REF_PREFIX + "dup"},
    )
    assert resp2.status_code == 409 and "不重复登记" in resp2.json()["detail"]


def test_api_upload_bad_ref_id_and_non_py(client):
    r1 = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_bad_id.py", SCRIPT_V1.encode("utf-8"), "text/x-python")},
        data={"ref_id": "Bad RefId!"},
    )
    assert r1.status_code == 400
    r2 = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_not_script.txt", b"hello", "text/plain")},
        data={"ref_id": REF_PREFIX + "txt"},
    )
    assert r2.status_code == 400


def test_api_import_catalog_idempotent(client, tmp_path):
    catalog = {
        "version": "1.0",
        "reference_files": [{
            "id": REF_PREFIX + "imported", "file": "t161_imported.py", "domain": "towed_array",
            "domain_rules": "towed_array_v1", "description": "导入单测",
            "sonar_types": ["towed_array"], "keywords": ["拖曳阵"],
        }],
    }
    (tmp_path / "catalog.yaml").write_text(yaml_dump(catalog), encoding="utf-8")
    (tmp_path / "t161_imported.py").write_text(SCRIPT_V1, encoding="utf-8")

    r1 = client.post("/api/v1/kb/references/import-catalog", json={"dir": str(tmp_path)})
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["created"] == 1 and body["unchanged"] == 0
    item = body["items"][0]
    assert item["action"] == "created" and item["status"] == "pending"
    assert item["content_sha256"] == _sha(SCRIPT_V1)  # hash/内容一致验收载体
    assert body["catalog_version"] == "1.0"

    # 幂等：同内容再导入 → unchanged，不产生新版本
    r2 = client.post("/api/v1/kb/references/import-catalog", json={"dir": str(tmp_path)})
    assert r2.status_code == 200
    assert r2.json()["created"] == 0 and r2.json()["unchanged"] == 1
    versions = ref_store.list_rows(REF_PREFIX + "imported")
    assert [v["version"] for v in versions] == [1]

    # 确认入库 → active（检索可见）
    r3 = client.post(f"/api/v1/kb/references/{item['row_id']}/confirm")
    assert r3.status_code == 200 and r3.json()["row"]["status"] == "active"


def test_api_import_bad_dir_400(client):
    r = client.post("/api/v1/kb/references/import-catalog", json={"dir": str(tmp_nonexistent())})
    assert r.status_code == 400


def test_api_rbac_and_content(client, monkeypatch):
    # 写端点：非 admin → 403（空角色/无 admin）
    anon = client.post(
        "/api/v1/kb/references",
        files={"file": ("t161_rbac.py", SCRIPT_V1.encode("utf-8"), "text/x-python")},
        data={"ref_id": REF_PREFIX + "rbac"},
        headers={"X-User-Id": "t161-viewer", "X-User-Roles": "viewer"},
    )
    assert anon.status_code == 403

    row = _insert("content", 1)
    # content 端点打真实 MinIO——monkeypatch download_text 返回同内容
    monkeypatch.setattr("storage.object_store.download_text",
                        lambda bucket, key: SCRIPT_V1, raising=False)
    r = client.get(f"/api/v1/kb/references/{row['id']}/content")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == SCRIPT_V1 and body["sha256_match"] is True
    assert body["content_sha256"] == _sha(SCRIPT_V1)

    # 审计规则命中：写操作落 audit_logs（旁路，此处只验证规则匹配本身）
    from api.audit import match_rule
    hit = match_rule("POST", f"/api/v1/kb/references/{row['id']}/confirm")
    assert hit is not None and hit[0] == "kb.ref_confirm"
    assert match_rule("POST", "/api/v1/kb/references")[0] == "kb.ref_create"
    assert match_rule("POST", "/api/v1/kb/references/import-catalog")[0] == "kb.ref_import"


# ----------------------------------------------------------------------
def yaml_dump(data: dict) -> str:
    import yaml as _yaml

    return _yaml.safe_dump(data, allow_unicode=True)


def tmp_nonexistent() -> str:
    import tempfile

    return tempfile.mkdtemp(suffix="-no-such-catalog") + "/missing"
