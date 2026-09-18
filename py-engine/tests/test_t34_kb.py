# -*- coding: utf-8 -*-
"""T3.4 图谱可视化接口单测：全图采样 / 邻域子图（fake store）+ API 行为。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AUTH = {"X-User-Id": "tester", "X-User-Roles": "admin"}


# ----------------------------------------------------------------------
# Neo4jStore 图谱查询（fake session）
# ----------------------------------------------------------------------
class FakeResult:
    def __init__(self, data):
        self._data = data

    def data(self):
        return self._data


class FakeSession:
    """按查询特征返回预设数据（只服务 graph_sample/neighborhood 的三条查询模式）。"""

    def __init__(self, routes):
        self._routes = routes  # list[(matcher, data)]，matcher: cypher 子串

    def run(self, cypher, **params):
        for matcher, data in self._routes:
            if matcher in cypher:
                return FakeResult(data)
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, routes):
        self._routes = routes

    def session(self):
        return FakeSession(self._routes)

    def verify_connectivity(self):
        return None


SAMPLE = {
    "nodes": [
        {"key": "k1", "label": "DesignConstraint", "name": "外径限制", "source_doc": "docA"},
        {"key": "k2", "label": "DesignParameter", "name": "阵元总数", "source_doc": "docA"},
    ],
    "edges": [{"source": "k1", "target": "k2", "rel_type": "LIMITS"}],
}


def test_graph_sample_with_fake_driver():
    from storage.neo4j_store import Neo4jStore

    routes = [("type(r) AS rel_type", SAMPLE["edges"]), ("labels(e)[0]", SAMPLE["nodes"])]
    store = Neo4jStore(driver=FakeDriver(routes))
    result = store.graph_sample(limit=10)
    assert len(result["nodes"]) == 2 and len(result["edges"]) == 1
    assert result["nodes"][0]["source_doc"] == "docA"  # 节点-文档互跳数据完整

    empty = Neo4jStore(driver=FakeDriver([("LIMIT $limit", [])]))
    assert empty.graph_sample() == {"nodes": [], "edges": []}  # 空图如实空集


def test_neighborhood_with_fake_driver():
    from storage.neo4j_store import Neo4jStore

    center = [{"key": "k1", "label": "DesignConstraint", "name": "外径限制",
               "source_doc": "docA", "deprecated": False}]
    routes = [
        ("WHERE e.key IS NOT NULL", center),
        ("-[r*1..", [{"key": "k2", "label": "DesignParameter", "name": "阵元总数",
                      "source_doc": "docA", "deprecated": False}]),
        ("type(r) AS rel_type", SAMPLE["edges"]),
    ]
    store = Neo4jStore(driver=FakeDriver(routes))
    result = store.neighborhood("k1", depth=2, limit=10)
    assert result["center"]["key"] == "k1"
    assert {n["key"] for n in result["nodes"]} == {"k1", "k2"}

    missing = Neo4jStore(driver=FakeDriver([("WHERE e.key IS NOT NULL", [])]))
    assert missing.neighborhood("nope") == {"center": None, "nodes": [], "edges": []}


# ----------------------------------------------------------------------
# API 行为
# ----------------------------------------------------------------------
@pytest.fixture()
def client(monkeypatch):
    from api.main import app

    return TestClient(app)


def test_graph_api_ok_and_unavailable(client, monkeypatch):
    import storage.neo4j_store as ns

    class OkStore:
        def available(self):
            return True

        def graph_sample(self, limit):
            return SAMPLE

        def neighborhood(self, key, depth, limit):
            return {"center": SAMPLE["nodes"][0], "nodes": SAMPLE["nodes"], "edges": SAMPLE["edges"]}

    monkeypatch.setattr(ns, "Neo4jStore", lambda: OkStore())
    r = client.get("/kb/graph", headers=AUTH)
    assert r.status_code == 200 and len(r.json()["nodes"]) == 2
    r = client.get("/kb/graph/neighborhood", headers=AUTH, params={"key": "k1", "depth": 2})
    assert r.status_code == 200 and r.json()["center"]["key"] == "k1"

    class DownStore:
        def available(self):
            return False

    monkeypatch.setattr(ns, "Neo4jStore", lambda: DownStore())
    r = client.get("/kb/graph", headers=AUTH)
    assert r.status_code == 503  # Neo4j 不可达如实降级（A-16）——读/写角色无关
