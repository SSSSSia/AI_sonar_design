# -*- coding: utf-8 -*-
"""T16.1 B1 检索 DB 源单测（PHASE-016 §3.5 步骤 2，契约=ADR-015）。

ref_store/object_store 全程 monkeypatch（选择器语义测试不打真库；真库装载由
test_t161_ref_store 承接）。覆盖：
- active 行命中全字段（catalog_version=ref-scripts@v{N}、sha256 同口径）
- **启停影响检索**（DEV-001 §1.4 B1 验收锚）：停用后同一实例下一选取即不注入
- DB 不可用 → db_unavailable 诚实降级，**绝不静默回退文件源**（受控包有脚本也不注入）
- 版本演进（确认新版本）→ 指纹变化整体重载 → 新内容新版本号
- 内容哈希与登记不符 → 按读取失败处理（不注入）
- 默认 file 模式行为不变（test_reference_governance 19 例为回归主锚，此处显式补 1 例）
"""

from __future__ import annotations

import hashlib
import sys
import uuid as uuid_mod
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_config
from storage import object_store, ref_store

SCRIPT_V1 = "REF_DB_V1 = 'towed pattern'\n"
SCRIPT_V2 = "REF_DB_V2 = 'towed pattern updated'\n"

DOC = "# 拖曳线列阵声呐 CAD 设计文档\n水听器布置"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso() -> str:
    return "2026-11-15T00:00:00+00:00"


def _db_row(version: int = 1, content: str = SCRIPT_V1, rid: str = "towed_array_system_twin",
            file_name: str = "towed_array_system_twin.py") -> dict:
    return {
        "id": str(uuid_mod.uuid4()), "ref_id": rid, "version": version, "file_name": file_name,
        "title": None, "description": "db 源单测", "domain": "towed_array",
        "domain_rules": "towed_array_v1", "sonar_types": ["拖曳线列阵声呐"],
        "keywords": ["水听器", "拖曳阵"], "bucket": "ref-scripts",
        "object_key": f"{rid}/v{version}/{file_name}", "content_sha256": _sha(content),
        "status": "active", "source_type": "catalog_import", "source_note": None,
        "created_by": "t161", "created_at": _iso(), "updated_at": _iso(),
        "_content": content,
    }


class FakeDb:
    """ref_store 替身：rows=active 行集合，error 模拟 DB 不可达，fp 随 rows 变化。"""

    def __init__(self):
        self.rows: list[dict] = []
        self.error = False

    @property
    def fp(self) -> list:
        return [(r["ref_id"], r["version"], r["status"], r["updated_at"]) for r in self.rows]

    def snapshot(self) -> list:
        if self.error:
            raise RuntimeError("db down (fake)")
        return self.fp

    def active(self) -> list[dict]:
        if self.error:
            raise RuntimeError("db down (fake)")
        return list(self.rows)


@pytest.fixture()
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(app_config, "REF_SOURCE", "db")
    monkeypatch.setattr(ref_store, "snapshot_fingerprint", fake.snapshot)
    monkeypatch.setattr(ref_store, "get_active_rows", fake.active)

    def fake_download(bucket: str, object_key: str) -> str:
        for r in fake.rows:
            if r["bucket"] == bucket and r["object_key"] == object_key:
                return r["_content"]
        raise RuntimeError(f"object not found: {object_key}")

    monkeypatch.setattr(object_store, "download_text", fake_download)
    return fake


def test_db_source_hit_fields(db):
    db.rows = [_db_row(version=1)]
    from cad_engine.reference import CodeSearchService

    svc = CodeSearchService()
    content, match = svc.select_reference(DOC)
    assert content == SCRIPT_V1
    assert match is not None
    assert match["ref_id"] == "towed_array_system_twin"
    assert match["catalog_version"] == "ref-scripts@v1"
    assert match["sha256"] == _sha(SCRIPT_V1)
    assert match["grade"] == "type_matched"
    assert match["domain"] == "towed_array"
    assert svc.last_reason is None


def test_disable_stops_injection_hot_reload(db):
    """启停影响检索（验收锚）：同一实例，停用（指纹变化）后下一选取即不注入。

    全部停用后 active 集为空 = 空索引 → reason=no_catalog（与 A0-2「no_catalog=目录缺失/
    坏 YAML/空索引」既有口径一致，db 源遵守相同领域策略）。
    """
    db.rows = [_db_row(version=1)]
    from cad_engine.reference import REASON_NO_CATALOG, CodeSearchService

    svc = CodeSearchService()
    content, match = svc.select_reference(DOC)
    assert match is not None

    db.rows = []  # 唯一 active 行被停用 → 快照指纹变化
    content2, match2 = svc.select_reference(DOC)
    assert content2 == "" and match2 is None
    assert svc.last_reason == REASON_NO_CATALOG


def test_db_unavailable_no_silent_file_fallback(db, monkeypatch, tmp_path):
    """DB 不可用 → db_unavailable 诚实降级；受控包/文件目录有脚本也不静默回退。"""
    from cad_engine.reference import REASON_DB_UNAVAILABLE, CodeSearchService

    # 文件源目录放一个可命中的对域 catalog——db 模式下绝不能因此注入
    d = tmp_path / "file_src"
    d.mkdir()
    (d / "towed_file_src.py").write_text("FILE_SRC = 1\n", encoding="utf-8")
    (d / "catalog.yaml").write_text(
        yaml.safe_dump({"version": "9.9", "reference_files": [{
            "id": "towed_file_src", "file": "towed_file_src.py", "domain": "towed_array",
            "sonar_types": ["拖曳线列阵声呐"], "keywords": ["水听器"],
        }]}, allow_unicode=True), encoding="utf-8")

    db.error = True
    svc = CodeSearchService(d)  # catalog_dir 参数在 db 模式下不参与装载
    content, match = svc.select_reference(DOC)
    assert content == "" and match is None
    assert svc.last_reason == REASON_DB_UNAVAILABLE

    # 恢复后自动重读（A0-1 失败恢复语义）
    db.error = False
    db.rows = [_db_row(version=1)]
    content2, match2 = svc.select_reference(DOC)
    assert match2 is not None and match2["catalog_version"] == "ref-scripts@v1"


def test_version_bump_reloads_new_content(db):
    """确认新版本 → 指纹变化整体重载 → 新内容 + 新版本号（缓存指纹随之变化）。"""
    db.rows = [_db_row(version=1)]
    from cad_engine.reference import CodeSearchService

    svc = CodeSearchService()
    _, match1 = svc.select_reference(DOC)
    assert match1["catalog_version"] == "ref-scripts@v1"

    db.rows = [_db_row(version=2, content=SCRIPT_V2)]  # v2 确认激活、v1 停用
    content2, match2 = svc.select_reference(DOC)
    assert content2 == SCRIPT_V2
    assert match2["catalog_version"] == "ref-scripts@v2"
    assert match2["sha256"] == _sha(SCRIPT_V2)


def test_hash_mismatch_treated_as_read_failed(db):
    """内容与登记哈希不符（篡改/半写）→ 按读取失败处理，不注入。"""
    db.rows = [_db_row(version=1)]
    db.rows[0]["content_sha256"] = "0" * 64
    from cad_engine.reference import REASON_READ_FAILED, CodeSearchService

    svc = CodeSearchService()
    content, match = svc.select_reference(DOC)
    assert content == "" and match is None
    assert svc.last_reason == REASON_READ_FAILED


def test_default_file_mode_unchanged(monkeypatch, tmp_path):
    """默认 file 模式行为不变（file_source 目录命中，catalog_version 为 catalog 版本号）。"""
    monkeypatch.setattr(app_config, "REF_SOURCE", "file")
    d = tmp_path / "ref_code"
    d.mkdir()
    (d / "towed_file.py").write_text("FILE_MODE = 1\n", encoding="utf-8")
    (d / "catalog.yaml").write_text(
        yaml.safe_dump({"version": "3.1", "reference_files": [{
            "id": "towed_file", "file": "towed_file.py", "domain": "towed_array",
            "sonar_types": ["拖曳线列阵声呐"], "keywords": ["水听器"],
        }]}, allow_unicode=True), encoding="utf-8")
    from cad_engine.reference import CodeSearchService

    svc = CodeSearchService(d)
    assert svc._source == "file"
    content, match = svc.select_reference(DOC)
    assert content == "FILE_MODE = 1\n"
    assert match["catalog_version"] == "3.1"          # file 语义：catalog.yaml version
    assert match["sha256"] == _sha("FILE_MODE = 1\n")


def test_ref_source_invalid_value_falls_to_file(monkeypatch):
    """REF_SOURCE 拼写错误 → 按 file 处理（不炸、不假 db）。"""
    monkeypatch.setattr(app_config, "REF_SOURCE", "database")
    from cad_engine.reference import CodeSearchService

    assert CodeSearchService()._source == "file"
