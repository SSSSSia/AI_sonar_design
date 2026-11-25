# -*- coding: utf-8 -*-
"""T17.4 范例库向量检索单测（PHASE-017；契约=ADR-016 §4）。

覆盖：sync_ref_vector（upsert 行形状/embed 失败 False 不抛/空材料不嵌）、
search_ref_vectors（rank 映射/失败空 dict）、delete_ref_vector、select_reference 融合
（同 grade 内 RRF 重排/type_matched 权威仍居首/向量扩召回 weak+ALLOW_WEAK 两态/默认关
逐字节旧行为/active 集合过滤）。

vector store 用 fake MilvusClient 注入（ChunkVectorStore(client=...)）；embed_texts
monkeypatch；select_reference 走 file 源 tmp 目录（沿 test_reference_governance 桩法），
REF_VECTOR_ENABLED 经 monkeypatch app_config 开关。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app_config
from cad_engine import ref_vector
from cad_engine.reference import CodeSearchService, REASON_NO_MATCH, REASON_WEAK_FILTERED


# ----------------------------------------------------------------------
# fake MilvusClient
# ----------------------------------------------------------------------
class FakeMilvus:
    def __init__(self, with_collection=True, hits=None):
        self.with_collection = with_collection
        self._hits = hits or []
        self.deleted: list[str] = []
        self.inserted: list[list[dict]] = []

    def has_collection(self, name):
        return self.with_collection

    def delete(self, collection, filter):
        self.deleted.append(filter)

    def insert(self, collection, rows):
        self.inserted.append(rows)
        return {"insert_count": len(rows)}

    def query(self, collection, filter, output_fields=None):
        return []

    def search(self, collection, data, limit, filter=None, output_fields=None):
        class _Hit(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)

        return [[_Hit(h) for h in self._hits[:limit]]]


def patch_store(monkeypatch, fake):
    from kb.vector_store import ChunkVectorStore

    monkeypatch.setattr(ref_vector, "_store",
                        lambda: ChunkVectorStore(collection="t174", client=fake))


def patch_embed(monkeypatch, result):
    monkeypatch.setattr("kb.embeddings.embed_texts",
                        (lambda texts: result) if not callable(result) else result)


# ----------------------------------------------------------------------
# sync / search / delete
# ----------------------------------------------------------------------
ROW = {"ref_id": "transducer_array", "version": 2, "title": "换能器基阵",
       "description": "4x6 平面阵"}


class TestSyncRefVector:
    def test_upsert_row_shape(self, monkeypatch):
        fake = FakeMilvus()
        patch_store(monkeypatch, fake)
        patch_embed(monkeypatch, [[0.1] * 4])
        assert ref_vector.sync_ref_vector(ROW, "import cadquery as cq\n# 基阵脚本") is True
        assert fake.deleted == ['doc_id == "transducer_array"']  # 先删（版本替换幂等）
        rows = fake.inserted[0]
        assert rows[0]["doc_id"] == "transducer_array"
        assert rows[0]["location"] == "v2"
        assert rows[0]["chunk_key"] == "transducer_array:0"

    def test_embed_fail_false_no_raise(self, monkeypatch):
        patch_store(monkeypatch, FakeMilvus())
        patch_embed(monkeypatch, None)
        assert ref_vector.sync_ref_vector(ROW, "content") is False  # 不抛（不阻塞入库）

    def test_embed_raise_caught(self, monkeypatch):
        patch_store(monkeypatch, FakeMilvus())
        patch_embed(monkeypatch, lambda texts: (_ for _ in ()).throw(RuntimeError("down")))
        assert ref_vector.sync_ref_vector(ROW, "content") is False

    def test_empty_material_skipped(self, monkeypatch):
        fake = FakeMilvus()
        patch_store(monkeypatch, fake)
        patch_embed(monkeypatch, [[0.1] * 4])
        assert ref_vector.sync_ref_vector({"ref_id": "x", "version": 1}, "") is False
        assert fake.inserted == []  # 空材料不嵌（A-16 不造空向量）


class TestSearchRefVectors:
    def test_rank_map(self, monkeypatch):
        fake = FakeMilvus(hits=[
            {"entity": {"doc_id": "b"}, "distance": 0.9},
            {"entity": {"doc_id": "a"}, "distance": 0.8},
            {"entity": {"doc_id": "b"}, "distance": 0.7},  # 同 ref_id 重复取首个名次
        ])
        patch_store(monkeypatch, fake)
        patch_embed(monkeypatch, [[0.1] * 4])
        ranks = ref_vector.search_ref_vectors("水听器阵列")
        assert ranks == {"b": (1, 0.9), "a": (2, 0.8)}

    def test_no_collection_empty(self, monkeypatch):
        patch_store(monkeypatch, FakeMilvus(with_collection=False))
        patch_embed(monkeypatch, [[0.1] * 4])
        assert ref_vector.search_ref_vectors("任意") == {}

    def test_embed_fail_empty(self, monkeypatch):
        patch_store(monkeypatch, FakeMilvus())
        patch_embed(monkeypatch, None)
        assert ref_vector.search_ref_vectors("任意") == {}


class TestDeleteRefVector:
    def test_delete_by_doc_id(self, monkeypatch):
        fake = FakeMilvus()
        patch_store(monkeypatch, fake)
        assert ref_vector.delete_ref_vector("towed") is True
        assert fake.deleted == ['doc_id == "towed"']

    def test_fail_no_raise(self, monkeypatch):
        def boom():
            raise RuntimeError("milvus down")

        monkeypatch.setattr(ref_vector, "_store", boom)
        assert ref_vector.delete_ref_vector("towed") is False


# ----------------------------------------------------------------------
# select_reference 融合（file 源 tmp 目录，沿 governance 桩法）
# ----------------------------------------------------------------------
def _make_ref_dir(tmp_path: Path, extra_entry: dict | None = None) -> Path:
    d = tmp_path / "ref_code"
    d.mkdir(exist_ok=True)
    (d / "towed_array_system_twin.py").write_text("# 拖曳阵脚本\nBODY_TOWED = 1\n", encoding="utf-8")
    entry = {
        "id": "towed_array_system_twin", "file": "towed_array_system_twin.py",
        "domain": "underwater", "domain_rules": "", "description": "拖曳线列阵声呐双锥主体",
        "sonar_types": ["拖曳线列阵声呐", "towed array sonar"], "keywords": ["拖曳", "水听器", "湿端"],
        "snippets": [],
    }
    entries = [entry]
    if extra_entry:
        entries.append(extra_entry)
        (d / extra_entry["file"]).write_text(f"# {extra_entry['id']} 脚本\nBODY = 2\n", encoding="utf-8")
    import yaml

    (d / "catalog.yaml").write_text(yaml.safe_dump(
        {"version": "1.0", "reference_files": entries}, allow_unicode=True), encoding="utf-8")
    return d


DESIGN_DOC_TOWED = "# 拖曳线列阵声呐 CAD 设计文档\n湿端水听器布置与拖曳缆"


class TestSelectReferenceFusion:
    def _svc(self, tmp_path, extra=None) -> CodeSearchService:
        return CodeSearchService(_make_ref_dir(tmp_path, extra))

    def _enable(self, monkeypatch):
        monkeypatch.setattr(app_config, "REF_VECTOR_ENABLED", True)

    def test_default_off_byte_identical(self, tmp_path, monkeypatch):
        """默认关：不触向量路径，行为与纯词法逐字节一致（回滚保证）。"""
        called = {"n": 0}
        monkeypatch.setattr(ref_vector, "search_ref_vectors",
                            lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or {})
        svc = self._svc(tmp_path)
        content, match = svc.select_reference(DESIGN_DOC_TOWED)
        assert match is not None and match["ref_id"] == "towed_array_system_twin"
        assert called["n"] == 0  # 关闭态零执行

    def test_rrf_reorders_within_same_grade(self, tmp_path, monkeypatch):
        """两个纯 weak 候选（title 不命中任何 sonar_types），向量名次反转注入选择
        （RRF 同 grade 内次级排序：词法 rank1+向量 rank3 被词法 rank2+向量 rank1 反超）。"""
        monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)
        self._enable(monkeypatch)
        extra = {"id": "entry_b", "file": "entry_b.py", "domain": "", "domain_rules": "",
                 "description": "", "sonar_types": [], "keywords": ["alpha"], "snippets": []}
        svc = self._svc(tmp_path, extra)
        # 重写 entry_a 为纯关键词条目（原 towed 条目的 sonar_types 会使 title 命中 type_matched
        # 跨 grade 不可重排——RRF 锚在 test_type_matched_still_beats_rrf 另测）
        import yaml

        d = svc._catalog_dir
        catalog = yaml.safe_load((d / "catalog.yaml").read_text(encoding="utf-8"))
        catalog["reference_files"][0] = {
            "id": "entry_a", "file": "towed_array_system_twin.py", "domain": "", "domain_rules": "",
            "description": "", "sonar_types": [], "keywords": ["alpha", "beta"], "snippets": [],
        }
        (d / "catalog.yaml").write_text(yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8")
        os.utime(d / "catalog.yaml", ns=(5_000_000_000, 5_000_000_000))
        svc = CodeSearchService(d)
        # 词法：A（alpha×3+beta）rank1 / B（alpha）rank2；向量：A rank3 / B rank1
        monkeypatch.setattr(ref_vector, "search_ref_vectors",
                            lambda q, top_k=5: {"entry_a": (3, 0.5), "entry_b": (1, 0.9)})
        content, match = svc.select_reference("# 通用主题 CAD 设计文档\nalpha alpha alpha beta")
        assert match is not None
        assert match["ref_id"] == "entry_b"  # RRF 反超：B=1/62+1/61 > A=1/61+1/63
        assert match["grade"] == "weak_keyword"

    def test_type_matched_still_beats_rrf(self, tmp_path, monkeypatch):
        """type_matched 权威优先不变：向量名次第一的 weak 候选不能压过词法 type_matched。"""
        monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)
        self._enable(monkeypatch)
        extra = {"id": "weak_but_vector_first", "file": "weak_but_vector_first.py", "domain": "",
                 "domain_rules": "", "description": "", "sonar_types": ["拖曳线列阵声呐"],
                 "keywords": ["拖曳"], "snippets": []}
        # 构造：weak 候选 = extra（keywords 命中「拖曳」但 title 未含其 sonar_types → 实为
        # type_matched…改为纯 keyword entry：sonar_types=[]，keywords=[拖曳])
        extra["sonar_types"] = []
        svc = self._svc(tmp_path, extra)
        monkeypatch.setattr(ref_vector, "search_ref_vectors",
                            lambda q, top_k=5: {"weak_but_vector_first": (1, 0.99),
                                                "towed_array_system_twin": (2, 0.5)})
        content, match = svc.select_reference(DESIGN_DOC_TOWED)
        assert match is not None and match["ref_id"] == "towed_array_system_twin"
        assert match["grade"] == "type_matched"  # 权威信号仍居首

    def test_vector_recall_extension_weak_two_states(self, tmp_path, monkeypatch):
        """向量扩召回：词法零候选时向量命中的 entry 以 weak 加入——ALLOW_WEAK=1 注入 /
        =0 过滤（reason=weak_filtered），无匹配不注入锚不破。"""
        self._enable(monkeypatch)
        # 查询词与词法完全无关（零词法候选）
        doc = "# 完全无关主题 CAD 设计文档\n量子纠缠黑洞"
        monkeypatch.setattr(ref_vector, "search_ref_vectors",
                            lambda q, top_k=5: {"towed_array_system_twin": (1, 0.9)})
        monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)
        svc = self._svc(tmp_path)
        content, match = svc.select_reference(doc)
        assert match is not None and match["ref_id"] == "towed_array_system_twin"
        assert match["grade"] == "weak_keyword"

        monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", False)
        svc2 = self._svc(tmp_path)
        content2, match2 = svc2.select_reference(doc)
        assert match2 is None and content2 == ""
        assert svc2.last_reason == REASON_WEAK_FILTERED  # 过滤原因如实（非 no_match）

    def test_vector_hit_filtered_by_active_entries(self, tmp_path, monkeypatch):
        """读侧 active 兜底：向量残留 ref_id 不在当前 entries → 不入选。"""
        self._enable(monkeypatch)
        svc = self._svc(tmp_path)
        monkeypatch.setattr(app_config, "REF_MATCH_ALLOW_WEAK", True)
        monkeypatch.setattr(ref_vector, "search_ref_vectors",
                            lambda q, top_k=5: {"disabled_ghost_ref": (1, 0.99)})
        content, match = svc.select_reference("# 完全无关 CAD 设计文档\n量子纠缠")
        assert match is None
        assert svc.last_reason == REASON_NO_MATCH  # 残留被过滤 → 回落无匹配语义

    def test_vector_failure_degrades_lexical(self, tmp_path, monkeypatch):
        """向量检索失败（空 dict）→ 纯词法行为不变。"""
        self._enable(monkeypatch)
        svc = self._svc(tmp_path)
        monkeypatch.setattr(ref_vector, "search_ref_vectors", lambda q, top_k=5: {})
        content, match = svc.select_reference(DESIGN_DOC_TOWED)
        assert match is not None and match["grade"] == "type_matched"
        assert match["sha256"]  # match 7 键契约不变
