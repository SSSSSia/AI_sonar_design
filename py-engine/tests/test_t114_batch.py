# -*- coding: utf-8 -*-
"""T11.4 单测：军标批量录入（文件解析 / 批量 upsert / API 行为）。"""

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}
VIEWER = {"X-User-Id": "v", "X-User-Roles": "viewer"}

CSV_UTF8 = "code,name,applies_to,requirement,expr,confidence,enabled\nGJB-A,条目A,外径,外径不超限,outer_diameter <= 325,high,是\nGJB-B,条目B,间距,间距下限,element_spacing >= 0.5,low,停用\n"
CSV_GBK = CSV_UTF8.encode("gbk")
CSV_BAD_BOOL = "code,name,enabled\nGJB-C,条目C,也许\n"


# ----------------------------------------------------------------------
# 文件解析（纯函数）
# ----------------------------------------------------------------------
def test_parse_csv_utf8_bom_and_bool_norm():
    from api.kb import _parse_batch_file

    rows = _parse_batch_file("standards.csv", b"\xef\xbb\xbf" + CSV_UTF8.encode("utf-8"))
    assert len(rows) == 2
    assert rows[0]["code"] == "GJB-A" and rows[0]["enabled"] is True
    assert rows[1]["enabled"] is False and rows[1]["confidence"] == "low"


def test_parse_csv_gbk_fallback():
    from api.kb import _parse_batch_file

    rows = _parse_batch_file("standards.csv", CSV_GBK)
    assert rows[0]["code"] == "GJB-A"


def test_parse_csv_bad_bool_raises():
    from api.kb import _parse_batch_file

    with pytest.raises(ValueError, match="enabled"):
        _parse_batch_file("s.csv", CSV_BAD_BOOL.encode("utf-8"))


def test_parse_xlsx_and_unsupported():
    import openpyxl

    from api.kb import _parse_batch_file

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["code", "name", "expr", "confidence"])
    ws.append(["GJB-X", "条目X", "n <= 128", "medium"])
    ws.append([None, "", None, None])  # 空行跳过
    buf = io.BytesIO()
    wb.save(buf)
    rows = _parse_batch_file("s.xlsx", buf.getvalue())
    assert len(rows) == 1 and rows[0]["expr"] == "n <= 128"

    with pytest.raises(ValueError, match="仅支持"):
        _parse_batch_file("s.pdf", b"%PDF")


# ----------------------------------------------------------------------
# batch_upsert（fake 连接：existing 只含 GJB-A）
# ----------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, existing):
        self._existing = existing

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return [{"code": k} for k in self._existing]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeTx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, existing):
        self._existing = existing

    def cursor(self):
        return _FakeCursor(self._existing)

    def transaction(self):
        return _FakeTx()  # psycopg3 嵌套事务（SAVEPOINT）的假实现

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_batch_upsert_counts_and_row_errors(monkeypatch):
    from storage import registry_store as rs

    existing = {"GJB-A"}
    monkeypatch.setattr(rs, "_connect", lambda: _FakeConn(existing))

    def fake_create(kind, fields):
        if fields.get("code") == "GJB-BAD":
            raise ValueError("confidence 只允许 high|medium|low: 中")
        return {"code": fields["code"]}

    monkeypatch.setattr(rs, "create_row", fake_create)
    result = rs.batch_upsert("standards", [
        {"code": "GJB-A", "name": "更新A"},          # existing → updated
        {"code": "GJB-B", "name": "新B"},            # → inserted
        {"code": "GJB-BAD", "confidence": "中"},     # → error
    ])
    assert result["total"] == 3 and result["inserted"] == 1 and result["updated"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 3 and "confidence" in result["errors"][0]["error"]


# ----------------------------------------------------------------------
# API 行为
# ----------------------------------------------------------------------
@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


def test_batch_api_requires_admin(client, monkeypatch):
    from solver import standards
    from storage import registry_store as rs

    calls = []
    monkeypatch.setattr(rs, "batch_upsert", lambda kind, rows: calls.append((kind, rows)) or
                        {"total": 1, "inserted": 1, "updated": 0, "errors": []})
    monkeypatch.setattr(standards, "reset_cache", lambda: None)
    r = client.post("/api/v1/kb/registry/standards/batch", headers=VIEWER,
                    json={"rows": [{"code": "X"}]})
    assert r.status_code == 403
    r = client.post("/api/v1/kb/registry/standards/batch", headers=ADMIN,
                    json={"rows": [{"code": "GJB-N", "name": "n"}]})
    assert r.status_code == 200 and r.json()["inserted"] == 1
    assert calls and calls[0][0] == "standards"


def test_batch_api_limit_and_empty(client):
    r = client.post("/api/v1/kb/registry/standards/batch", headers=ADMIN, json={"rows": []})
    assert r.status_code == 400
    r = client.post("/api/v1/kb/registry/standards/batch", headers=ADMIN,
                    json={"rows": [{"code": f"G{i}"} for i in range(501)]})
    assert r.status_code == 400 and "500" in r.json()["detail"]


def test_import_dry_run_validates_without_db(client):
    r = client.post("/api/v1/kb/registry/standards/import?dry_run=true", headers=ADMIN,
                    files={"file": ("s.csv", CSV_UTF8.encode("utf-8"), "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and len(body["rows"]) == 2
    assert body["problems"] == []
    # confidence 非法 → problems 如实带 Excel 行号（首行表头 → 数据从 2 起）
    bad = "code,name,confidence\nGJB-Z,坏条目,极高\n"
    r = client.post("/api/v1/kb/registry/standards/import?dry_run=true", headers=ADMIN,
                    files={"file": ("s.csv", bad.encode("utf-8"), "text/csv")})
    assert r.json()["problems"] == [{"row": 2, "error": "confidence 非法: 极高"}]
