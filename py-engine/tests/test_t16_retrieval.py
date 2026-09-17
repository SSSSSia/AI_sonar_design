"""T1.6 单测：三级降级检索（A-14）。不依赖真实 GraphRAG/Milvus。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge.service import GraphRAGService, RetrievalResult


class FakeSdk:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[str] = []

    def query_local_sync(self, q):
        self.calls.append("local")
        if "local" in self.fail:
            return {"success": False, "error": "boom"}
        return {"success": True, "response": f"LOCAL::{q}"}

    def query_basic_sync(self, q):
        self.calls.append("basic")
        if "basic" in self.fail:
            return {"success": False, "error": "boom"}
        return {"success": True, "response": f"BASIC::{q}"}

    def query_batch_sync_with_fallback(self, queries):
        out = []
        for q in queries:
            self.calls.append("local")
            if "local" in self.fail:
                out.append({"success": False, "error": "boom"})
            else:
                out.append({"success": True, "response": f"LOCAL::{q}"})
        return out


KB_TEXT = "拖曳线列阵声呐由阵列传感器、前置放大器与数据采集系统组成。\n\n湿端结构决定阵列的流体噪声特性。\n\nCadQuery 用于参数化建模。"


def make_service(tmp_path: Path, sdk) -> GraphRAGService:
    inp = tmp_path / "kb" / "input"
    inp.mkdir(parents=True)
    (inp / "kb.txt").write_text(KB_TEXT, encoding="utf-8")
    svc = GraphRAGService(kb_dir=tmp_path / "kb")
    svc.sdk = sdk
    return svc


class TestThreeLevelFallback:
    def test_level1_local_hit(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk())
        r = svc.query("阵列组成")
        assert r.level == "local"
        assert r.content == "LOCAL::阵列组成"
        assert r.degraded_from == []

    def test_level2_basic_after_local_fail(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk(fail={"local"}))
        r = svc.query("阵列组成")
        assert r.level == "basic"
        assert r.degraded_from == ["local"]

    def test_level3_text_after_both_fail(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk(fail={"local", "basic"}))
        r = svc.query("拖曳线列阵声呐 阵列传感器")
        assert r.level == "text"
        assert r.degraded_from == ["local", "basic"]
        assert "阵列传感器" in r.content

    def test_no_sdk_no_text_unavailable(self, tmp_path):
        svc = GraphRAGService(kb_dir=tmp_path / "empty")
        svc.sdk = None
        r = svc.query("任意")
        assert r.level == "unavailable"
        assert not r.ok

    def test_sdk_exception_degrades_not_raises(self, tmp_path):
        class ExplodingSdk:
            def query_local_sync(self, q):
                raise RuntimeError("down")

            def query_basic_sync(self, q):
                raise RuntimeError("down")

        svc = make_service(tmp_path, ExplodingSdk())
        r = svc.query("组成")
        assert r.level == "text"  # 异常不中断，逐级降级（A-14）


class TestBatch:
    def test_batch_mixed(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk())
        rs = svc.query_batch(["q1", "q2"])
        assert all(r.level == "local" for r in rs)

    def test_batch_degrade_to_text(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk(fail={"local"}))
        rs = svc.query_batch(["拖曳线列阵 组成", "组成"])
        assert all(r.level == "text" for r in rs)
        assert rs[0].degraded_from == ["local", "basic"]


class TestChineseTextSearch:
    def test_chinese_bigram_match(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk(fail={"local", "basic"}))
        r = svc.query("湿端结构")
        assert r.level == "text"
        assert "流体噪声" in r.content

    def test_no_match_returns_overview(self, tmp_path):
        svc = make_service(tmp_path, FakeSdk(fail={"local", "basic"}))
        r = svc.query("量子纠缠")
        assert "未找到精确匹配" in r.content


class TestResultContract:
    def test_to_dict_shape(self):
        r = RetrievalResult("c", "text", ["local", "basic"])
        d = r.to_dict()
        assert set(d) == {"content", "level", "degraded_from"}
