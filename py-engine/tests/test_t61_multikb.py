# -*- coding: utf-8 -*-
"""T6.1 多库管理单测（PHASE-006）：kb_scope 范围解析 + kb_store 归属纯逻辑 + API 行为。

DB 薄封装走真库（tests 与现网共用 sonarcad 库，测试库/文档用唯一前缀并在 teardown 清理）；
纯逻辑（kb_scope 缓存、API 参数分支）走 fake/monkeypatch，不打库。
"""

from __future__ import annotations

import uuid

import pytest

from storage import kb_scope, kb_store


# ----------------------------------------------------------------------
# kb_scope：kb_ids → doc 集合（fake kb_store，不打库）
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_scope_cache():
    kb_scope.invalidate()
    yield
    kb_scope.invalidate()


class FakeScopeStore:
    def __init__(self, assignments: dict[str, list[str]]):
        self.assignments = assignments  # base_id -> [doc_id]
        self.calls = 0

    def list_documents(self, limit=100, base_id=None, unassigned=False, doc_ids=None):
        self.calls += 1
        return [{"doc_id": d} for d in self.assignments.get(base_id, [])]

    def _connect(self):  # 不会被走到（_all_assigned_doc_ids 被替换）
        raise AssertionError("fake 不应触库")


@pytest.fixture
def patch_all_assigned(monkeypatch):
    def _patch(all_assigned: list[str]):
        monkeypatch.setattr(kb_scope, "_all_assigned_doc_ids", lambda: all_assigned)

    return _patch


def test_scope_specific_bases_union(patch_all_assigned):
    store = FakeScopeStore({"a": ["d1", "d2"], "b": ["d2", "d3"]})
    patch_all_assigned([])
    orig = kb_scope.kb_store
    kb_scope.kb_store = store
    try:
        assert kb_scope.resolve_doc_ids(["a", "b"]) == ["d1", "d2", "d3"]
    finally:
        kb_scope.kb_store = orig


def test_scope_empty_means_all_assigned_excludes_unassigned(patch_all_assigned):
    patch_all_assigned(["d1", "d2"])  # d3 无归属（未归库）→ 不出现
    assert kb_scope.resolve_doc_ids(None) == ["d1", "d2"]
    assert kb_scope.resolve_doc_ids([]) == ["d1", "d2"]


def test_scope_ttl_cache_and_invalidate(patch_all_assigned):
    store = FakeScopeStore({"a": ["d1"]})
    patch_all_assigned(["d1"])
    orig = kb_scope.kb_store
    kb_scope.kb_store = store
    try:
        kb_scope.resolve_doc_ids(["a"])
        kb_scope.resolve_doc_ids(["a"])
        assert store.calls == 1  # TTL 内命中缓存
        kb_scope.invalidate()
        kb_scope.resolve_doc_ids(["a"])
        assert store.calls == 2  # 失效后重取
    finally:
        kb_scope.kb_store = orig


def test_scope_db_error_returns_empty(monkeypatch):
    class BoomStore:
        def list_documents(self, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(kb_scope, "kb_store", BoomStore())
    assert kb_scope.resolve_doc_ids(["a"]) == []  # 如实零命中，不抛


# ----------------------------------------------------------------------
# kb_store 归属操作（真库，唯一前缀 + teardown 清理）
# ----------------------------------------------------------------------
@pytest.fixture
def real_base():
    name = f"mk-ut-{uuid.uuid4().hex[:8]}"
    row = kb_store.create_base(name, "unittest", created_by="ut")
    yield row
    kb_store.delete_base(row["id"])


@pytest.fixture
def real_docs():
    ids = []
    for i in range(2):
        d = kb_store.create_document(
            filename=f"mk-ut-{uuid.uuid4().hex[:6]}.txt", fmt="txt", size_bytes=1,
            bucket="kb-docs", object_key="ut", uploaded_by="ut",
        )
        ids.append(d["doc_id"])
    yield ids
    for doc_id in ids:
        kb_store.delete_document(doc_id)  # CASCADE 清归属


def test_base_crud_and_default_guard(real_base):
    row = kb_store.update_base(real_base["id"], {"description": "updated"})
    assert row["description"] == "updated"
    assert [b["id"] for b in kb_store.list_bases() if b["id"] == real_base["id"]]


def test_assignment_add_remove_idempotent_and_multi(real_base, real_docs):
    d1, d2 = real_docs
    assert kb_store.add_docs_to_base(real_base["id"], [d1, d2], added_by="ut") == 2
    assert kb_store.add_docs_to_base(real_base["id"], [d1], added_by="ut") == 0  # 幂等
    assert sorted(d["doc_id"] for d in kb_store.list_documents(base_id=real_base["id"])) == sorted([d1, d2])
    assert kb_store.doc_base_ids(d1) == [real_base["id"]]

    # 多归属：默认库也含 d1（现网种子已归），doc_base_ids 排序按 added_at
    default_id = [b["id"] for b in kb_store.list_bases() if b["is_default"]][0]
    kb_store.add_docs_to_base(default_id, [d1], added_by="ut")
    assert set(kb_store.doc_base_ids(d1)) == {real_base["id"], default_id}

    # 移出测试库仅解除本库引用，默认库仍在
    assert kb_store.remove_docs_from_base(real_base["id"], [d1]) == 1
    assert default_id in kb_store.doc_base_ids(d1)
    kb_store.remove_docs_from_base(default_id, [d1])  # 还原默认库种子态

    # 无归属 → unassigned 池可见
    kb_store.remove_docs_from_base(real_base["id"], [d2])
    assert d2 in [d["doc_id"] for d in kb_store.list_documents(unassigned=True)]


def test_delete_base_releases_docs(real_base, real_docs):
    d1 = real_docs[0]
    kb_store.add_docs_to_base(real_base["id"], [d1])
    deleted, refs = kb_store.delete_base(real_base["id"])
    assert deleted and refs == 1
    assert kb_store.get_base(real_base["id"]) is None
    assert kb_store.doc_base_ids(d1) == []  # 引用解除（文档行仍在，fixture 清理）
