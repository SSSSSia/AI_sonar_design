# -*- coding: utf-8 -*-
"""T17.5 渲染服务化+多模态索引单测（PHASE-017；契约=ADR-016 §5）。

覆盖：llm/vision.describe_images（mock httpx，沿 test_t35_ocr 函数级 mock 先例）、
render.render_step_views（monkeypatch subprocess.run，真实 cadquery 渲染不进单测）、
ref_visual.sync 步骤链逐步骤如实（sandbox/render/upload/caption/embed 五级失败语义+
全通 upsert 行形状 caption 落 text 字段）、delete/search、API search-by-image
（flag 关 409/开态命中排序+active 过滤+caption 回显）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from cad_engine import ref_visual, render
from llm import vision as llm_vision

PNG = b"\x89PNG\r\n\x1a\nFAKE"
ROW = {"ref_id": "transducer_array", "version": 2, "title": "换能器基阵"}


# ----------------------------------------------------------------------
# llm/vision.py
# ----------------------------------------------------------------------
class FakeProvider:
    base_url = "http://vllm.test/v1"

    class _Key:
        def get_secret_value(self):
            return "sk-test"

    api_key = _Key()


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class TestDescribeImages:
    def _patch_provider(self, monkeypatch):
        import llm.adapter as adapter

        monkeypatch.setattr(adapter, "active_provider", lambda: FakeProvider())

    def test_success(self, monkeypatch):
        self._patch_provider(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResp(payload={"choices": [{"message": {"content": " 圆柱耐压段+收缩锥 "}}],
                                     "usage": {"total_tokens": 100}})

        monkeypatch.setattr(llm_vision.httpx, "post", fake_post)
        out = llm_vision.describe_images([PNG], "描述")
        assert out == "圆柱耐压段+收缩锥"  # strip
        content = captured["json"]["messages"][0]["content"]
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[-1] == {"type": "text", "text": "描述"}
        assert captured["json"]["temperature"] == 0.2 and captured["json"]["stream"] is False

    def test_http_error_none(self, monkeypatch):
        self._patch_provider(monkeypatch)
        monkeypatch.setattr(llm_vision.httpx, "post",
                            lambda *a, **kw: FakeResp(status_code=500, text="boom"))
        assert llm_vision.describe_images([PNG], "p") is None

    def test_exception_none(self, monkeypatch):
        self._patch_provider(monkeypatch)
        def boom(*a, **kw):
            raise RuntimeError("conn reset")

        monkeypatch.setattr(llm_vision.httpx, "post", boom)
        assert llm_vision.describe_images([PNG], "p") is None

    def test_no_provider_none(self, monkeypatch):
        import llm.adapter as adapter

        def no_provider():
            raise RuntimeError("no providers")

        monkeypatch.setattr(adapter, "active_provider", no_provider)
        assert llm_vision.describe_images([PNG], "p") is None

    def test_empty_images_none(self):
        assert llm_vision.describe_images([], "p") is None


# ----------------------------------------------------------------------
# cad_engine/render.py
# ----------------------------------------------------------------------
class FakeProc:
    def __init__(self, returncode=0, stdout="rendered", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestRenderStepViews:
    def _patch_run(self, monkeypatch, proc, write_png=False):
        def run(cmd, capture_output, text, timeout):
            if write_png:
                Path(cmd[4]).write_bytes(PNG)  # cmd=[py,-c,code,step,out_png]
            return proc

        monkeypatch.setattr(render.subprocess, "run", run)

    def test_success(self, monkeypatch):
        self._patch_run(monkeypatch, FakeProc(), write_png=True)
        out = render.render_step_views("/fake/step.step")
        assert out["ok"] is True and out["png"] == PNG and out["error"] == ""

    def test_fail_returncode(self, monkeypatch):
        self._patch_run(monkeypatch, FakeProc(returncode=1, stderr="import error"))
        out = render.render_step_views("/fake.step")
        assert out["ok"] is False and "import error" in out["error"] and out["png"] is None

    def test_timeout(self, monkeypatch):
        def run(cmd, capture_output, text, timeout):
            raise render.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        monkeypatch.setattr(render.subprocess, "run", run)
        out = render.render_step_views("/fake.step")
        assert out["ok"] is False and "timeout" in out["error"]

    def test_no_png_product(self, monkeypatch):
        self._patch_run(monkeypatch, FakeProc())  # stdout rendered 但未写文件
        out = render.render_step_views("/fake.step")
        assert out["ok"] is False and "未产出" in out["error"]


# ----------------------------------------------------------------------
# cad_engine/ref_visual.py（fake store 注缝同 T17.4）
# ----------------------------------------------------------------------
class FakeVS:
    def __init__(self, hits=None):
        self._hits = hits or []
        self.deleted: list[str] = []
        self.inserted: list[list[dict]] = []

    def has_collection(self, name):
        return True

    def delete(self, collection, filter):
        self.deleted.append(filter)

    def query(self, collection, filter, output_fields=None):
        return []

    def insert(self, collection, rows):
        self.inserted.append(rows)
        return {"insert_count": len(rows)}

    def search(self, collection, data, limit, filter=None, output_fields=None):
        # MilvusClient 契约：results = [每查询一列表]（results[0] 才是 hits）
        return [[dict(h) for h in self._hits[:limit]]]


def patch_vs(monkeypatch, fake):
    from kb.vector_store import ChunkVectorStore

    monkeypatch.setattr(ref_visual, "_store",
                        lambda: ChunkVectorStore(collection="t175", client=fake))


class TestSyncRefVisual:
    ROW = {"ref_id": "transducer_array", "version": 2}

    def _patch_chain(self, monkeypatch, *, sandbox_ok=True, render_ok=True,
                     upload_fail=False, caption="平面基阵 4x6 圆柱阵元",
                     embed=[[0.1] * 4]):
        from cad_engine import sandbox

        monkeypatch.setattr(sandbox, "run_in_subprocess",
                            lambda code, time_limit_s=120: (
                                {"ok": True, "step_files": ["/tmp/m.step"]} if sandbox_ok
                                else {"ok": False, "error": "SyntaxError line 4"}))
        monkeypatch.setattr(render, "render_step_views",
                            lambda step: ({"ok": True, "png": PNG, "error": ""} if render_ok
                                          else {"ok": False, "png": None, "error": "import error"}))
        uploads = []
        monkeypatch.setattr("storage.object_store.upload_bytes",
                            lambda data, bucket, key, ct="": uploads.append((bucket, key)) or {})
        monkeypatch.setattr(llm_vision, "describe_images",
                            (lambda imgs, prompt, model=None: caption) if caption is not None
                            else (lambda imgs, prompt, model=None: None))
        patch_vs(monkeypatch, FakeVS())
        if embed is None:
            monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: None)
        else:
            monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: embed)
        return uploads

    def test_full_pass_row_shape(self, monkeypatch):
        uploads = self._patch_chain(monkeypatch)
        out = ref_visual.sync_ref_visual(self.ROW, "import cadquery as cq\n...")
        assert out["ok"] is True and out["stage"] == "done"
        assert uploads == [("ref-scripts", "transducer_array/v2/views/views.png")]
        fake = None
        # upsert 行形状：从 _store 侧验证
        from kb.vector_store import ChunkVectorStore
        seen = {}

        class Spy(FakeVS):
            def insert(self, collection, rows):
                seen["rows"] = rows
                return {"insert_count": len(rows)}

        monkeypatch.setattr(ref_visual, "_store",
                            lambda: ChunkVectorStore(collection="t175", client=Spy()))
        ref_visual.sync_ref_visual(self.ROW, "code")
        rows = seen["rows"]
        assert rows[0]["doc_id"] == "transducer_array"
        assert rows[0]["location"] == "v2"
        assert "平面基阵" in rows[0]["text"]  # caption 全文落 text=人工可核对（A-16）

    def test_sandbox_fail_stage(self, monkeypatch):
        self._patch_chain(monkeypatch, sandbox_ok=False)
        out = ref_visual.sync_ref_visual(self.ROW, "code")
        assert out["ok"] is False and out["stage"] == "sandbox" and "SyntaxError" in out["detail"]

    def test_render_fail_stage(self, monkeypatch):
        self._patch_chain(monkeypatch, render_ok=False)
        out = ref_visual.sync_ref_visual(self.ROW, "code")
        assert out["ok"] is False and out["stage"] == "render" and "import error" in out["detail"]

    def test_upload_fail_stage(self, monkeypatch):
        self._patch_chain(monkeypatch, upload_fail=True)
        import storage.object_store as obs

        monkeypatch.setattr(obs, "upload_bytes",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("minio down")))
        out = ref_visual.sync_ref_visual(self.ROW, "code")
        assert out["ok"] is False and out["stage"] == "upload"

    def test_caption_fail_stage(self, monkeypatch):
        self._patch_chain(monkeypatch, caption=None)
        out = ref_visual.sync_ref_visual(self.ROW, "code")
        assert out["ok"] is False and out["stage"] == "caption"

    def test_embed_fail_stage(self, monkeypatch):
        self._patch_chain(monkeypatch, embed=None)
        out = ref_visual.sync_ref_visual(self.ROW, "code")
        assert out["ok"] is False and out["stage"] == "embed"


class TestVisualSearch:
    def test_rank_and_caption(self, monkeypatch):
        fake = FakeVS(hits=[
            {"entity": {"doc_id": "a", "location": "v1", "text": "拖鱼描述"}, "distance": 0.9},
            {"entity": {"doc_id": "b", "location": "v1", "text": "浮标描述"}, "distance": 0.7},
        ])
        patch_vs(monkeypatch, fake)
        monkeypatch.setattr(llm_vision, "describe_images", lambda imgs, prompt, model=None: "查询图描述")
        monkeypatch.setattr("kb.embeddings.embed_texts", lambda texts: [[0.1] * 4])
        out = ref_visual.search_visual_by_image(PNG)
        assert list(out) == ["a", "b"]
        assert out["a"][2] == "拖鱼描述"

    def test_caption_fail_empty(self, monkeypatch):
        patch_vs(monkeypatch, FakeVS())
        monkeypatch.setattr(llm_vision, "describe_images", lambda imgs, prompt, model=None: None)
        assert ref_visual.search_visual_by_image(PNG) == {}

    def test_empty_png(self, monkeypatch):
        assert ref_visual.search_visual_by_image(b"") == {}


# ----------------------------------------------------------------------
# API /kb/references/search-by-image
# ----------------------------------------------------------------------
HDRS = {"X-User-Id": "t175", "X-User-Roles": "designer"}


class TestSearchByImageEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import app

        return TestClient(app)

    def test_flag_off_409(self, monkeypatch):
        import app_config

        monkeypatch.setattr(app_config, "REF_VISUAL_ENABLED", False)
        resp = self._client().post(
            "/api/v1/kb/references/search-by-image",
            files={"image": ("x.png", PNG, "image/png")}, headers=HDRS)
        assert resp.status_code == 409 and "未启用" in resp.json()["detail"]

    def test_hit_sorted_and_active_filtered(self, monkeypatch):
        import app_config

        monkeypatch.setattr(app_config, "REF_VISUAL_ENABLED", True)
        monkeypatch.setattr(ref_visual, "search_visual_by_image", lambda png, top_k=5: {
            "ghost_ref": (1, 0.95, "残留描述"),
            "towed_array_system_twin": (2, 0.8, "拖鱼：圆柱耐压段+稳定翼"),
        })

        import storage.ref_store as rs

        monkeypatch.setattr(rs, "get_active_rows",
                            lambda: [{"ref_id": "towed_array_system_twin"}])
        resp = self._client().post(
            "/api/v1/kb/references/search-by-image",
            files={"image": ("x.png", PNG, "image/png")}, headers=HDRS)
        assert resp.status_code == 200
        data = resp.json()
        assert [m["ref_id"] for m in data["matches"]] == ["towed_array_system_twin"]
        assert data["matches"][0]["active"] is True
        assert "拖鱼" in data["matches"][0]["caption"]
        assert data["caption"] == "拖鱼：圆柱耐压段+稳定翼"  # caption 回显=人工可核对

    def test_empty_png_422(self, monkeypatch):
        import app_config

        monkeypatch.setattr(app_config, "REF_VISUAL_ENABLED", True)
        resp = self._client().post(
            "/api/v1/kb/references/search-by-image",
            files={"image": ("x.png", b"", "image/png")}, headers=HDRS)
        assert resp.status_code == 422
