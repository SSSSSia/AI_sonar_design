# -*- coding: utf-8 -*-
"""T17.7 评测指标纯函数单测（手算例+边界）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kb.eval_metrics import ndcg_at_k, mrr, recall_at_k

REL = {"d1:段落1", "d2:段落2"}


class TestRecallAtK:
    def test_full_hit(self):
        assert recall_at_k(["d1:段落1", "d2:段落2", "x"], REL, k=5) == 1.0

    def test_partial(self):
        assert recall_at_k(["d1:段落1", "x", "y"], REL, k=5) == 0.5

    def test_k_bounds(self):
        # 第二个相关项在 k=1 窗口外
        assert recall_at_k(["d1:段落1", "d2:段落2"], REL, k=1) == 0.5

    def test_no_hit_and_empty_relevant(self):
        assert recall_at_k(["x", "y"], REL, k=5) == 0.0
        assert recall_at_k(["d1:段落1"], set(), k=5) == 0.0  # 不造指标（A-16）


class TestMrr:
    def test_first_rank(self):
        assert mrr(["d1:段落1", "d2:段落2"], REL) == 1.0

    def test_second_rank(self):
        assert mrr(["x", "d2:段落2"], REL) == 0.5

    def test_no_hit(self):
        assert mrr(["x", "y"], REL) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking(self):
        assert ndcg_at_k(["d1:段落1", "d2:段落2", "x"], REL, k=5) == 1.0

    def test_second_rel_outside_window(self):
        # k=2 窗口内只命中 rank1 的 d2（d1 被挤出）→ DCG=1, IDCG=1+1/log2(3)≈1.6309 → ≈0.613
        v = ndcg_at_k(["d2:段落2", "x", "d1:段落1"], REL, k=2)
        assert abs(v - 0.6131) < 1e-3

    def test_single_rel_at_rank2(self):
        # rel 单条在 rank2：DCG=1/log2(3), IDCG=1 → ≈0.6309
        assert abs(ndcg_at_k(["x", "d1:段落1"], {"d1:段落1"}, k=5) - 0.6309) < 1e-3

    def test_no_hit(self):
        assert ndcg_at_k(["x"], REL, k=5) == 0.0
