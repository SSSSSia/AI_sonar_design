# -*- coding: utf-8 -*-
"""检索评测指标纯函数（T17.7 评测实验台，PHASE-017；二值相关标注口径）。

identity 口径：ranked_ids 与 relevant 均为 (doc_id + ":" + location) 字符串
（kb_chunks.location="段落N"，各配置统一）；图实体项（kind=graph）由调用方过滤后
不计入（chunk 级指标）。nDCG 折损沿用 2^rel-1 / log2(rank+1) 标准形（rel∈{0,1}）。
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def recall_at_k(ranked_ids: Sequence[str], relevant: Iterable[str], k: int = 5) -> float:
    """top-k 命中相关项比例；relevant 为空 → 0.0（不造指标，A-16）。"""
    rel = set(relevant)
    if not rel:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in rel)
    return hits / len(rel)


def mrr(ranked_ids: Sequence[str], relevant: Iterable[str]) -> float:
    """首个相关项倒数排名；无相关命中 → 0.0。"""
    rel = set(relevant)
    if not rel:
        return 0.0
    for rank, rid in enumerate(ranked_ids, start=1):
        if rid in rel:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: Sequence[str], relevant: Iterable[str], k: int = 5) -> float:
    """二值相关 nDCG@k；relevant 为空 → 0.0。"""
    rel = set(relevant)
    if not rel:
        return 0.0
    dcg = sum(
        1.0 / math.log2(i + 2)
        for i, rid in enumerate(ranked_ids[:k])
        if rid in rel
    )
    ideal_hits = min(len(rel), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg else 0.0
