# -*- coding: utf-8 -*-
"""T18.6 gate2 裁决材料读侧单测（PHASE-018；契约=ADR-017）。

GET /cad/sessions/{sid}/vision-views：三视图 PNG 直出四态（checked 200 / 未启用 404 /
skipped 404 带原因 / views 缺位 404）+ 鉴权 + 版本头。TestClient+monkeypatch 存储层，
全离线（t123/t145 形态先例）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = {"X-User-Id": "a", "X-User-Roles": "admin"}
PNG = b"\x89PNG-fake-views"
STEP_REF = {"bucket": "step-models", "object_key": "s1/a3/tok/sonar_model.step"}
VIEWS_REF = {"bucket": "step-models", "object_key": "s1/a3/tok/views.png", "sha256": "d" * 64}
VC_CHECKED: dict[str, Any] = {"status": "checked", "verdict_text": "未见明显异常",
                              "views_png": dict(VIEWS_REF)}


def _ir3(vc: dict[str, Any] | None) -> dict[str, Any]:
    ir3 = {"cadquery_script": "x = 1", "step_file": dict(STEP_REF), "features": []}
    if vc is not None:
        ir3["vision_check"] = vc
    return ir3


def _client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


def _patch_ir3(monkeypatch, ir3: dict[str, Any] | None, version: int = 3):
    monkeypatch.setattr("storage.ir_store.get_latest_snapshot",
                        lambda sid, t: ir3 if ir3 else None)
    monkeypatch.setattr("storage.ir_store.get_latest_version", lambda sid, t: version)
    monkeypatch.setattr("storage.ir_store.get_snapshot",
                        lambda sid, t, v: ir3 if ir3 else None)


class TestVisionViewsEndpoint:
    def test_checked_200_png(self, monkeypatch):
        _patch_ir3(monkeypatch, _ir3(dict(VC_CHECKED)), version=3)
        monkeypatch.setattr("storage.object_store.download_bytes", lambda b, k: PNG)
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views", headers=ADMIN)
        assert resp.status_code == 200
        assert resp.content == PNG
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.headers["X-IR-Version"] == "3"

    def test_versioned_200(self, monkeypatch):
        _patch_ir3(monkeypatch, _ir3(dict(VC_CHECKED)), version=2)
        monkeypatch.setattr("storage.object_store.download_bytes", lambda b, k: PNG)
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views?version=2", headers=ADMIN)
        assert resp.status_code == 200
        assert resp.headers["X-IR-Version"] == "2"

    def test_no_vision_check_404_off(self, monkeypatch):
        """flag 关/旧快照（无 vision_check 字段）→404 如实「未启用」。"""
        _patch_ir3(monkeypatch, _ir3(None))
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views", headers=ADMIN)
        assert resp.status_code == 404
        assert "未启用" in resp.json()["detail"]

    def test_skipped_404_with_detail(self, monkeypatch):
        _patch_ir3(monkeypatch, _ir3({"status": "skipped",
                                      "detail": "render_failed: tessellate timeout"}))
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views", headers=ADMIN)
        assert resp.status_code == 404
        assert "render_failed" in resp.json()["detail"]

    def test_views_png_missing_404(self, monkeypatch):
        """checked 但上传留档缺位→404 如实（判词仍在 IR3 可读）。"""
        _patch_ir3(monkeypatch, _ir3({"status": "checked", "verdict_text": "t"}))
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views", headers=ADMIN)
        assert resp.status_code == 404
        assert "留档缺位" in resp.json()["detail"]

    def test_foreign_bucket_rejected(self, monkeypatch):
        _patch_ir3(monkeypatch, _ir3({"status": "checked", "verdict_text": "t",
                                      "views_png": {"bucket": "kb-docs",
                                                    "object_key": "x/a.png"}}))
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views", headers=ADMIN)
        assert resp.status_code == 404
        assert "桶非法" in resp.json()["detail"]

    def test_requires_user_401(self, monkeypatch):
        _patch_ir3(monkeypatch, _ir3(dict(VC_CHECKED)))
        resp = _client().get("/api/v1/cad/sessions/s-1/vision-views")
        assert resp.status_code == 401
