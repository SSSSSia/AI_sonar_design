# -*- coding: utf-8 -*-
"""T17.7 检索评测 harness（PHASE-017 收官；毕设对照实验数据）。

五配置对照（同一评测集，指标 Recall@5 / MRR / nDCG@5 + 延迟 P50/P95）：
  vector       = GraphRAGService._try_milvus 单路（sdk=None 确定性跳过 SDK 级——SDK 不在对照范围，如实）
  bm25         = kb.bm25.bm25_search 单路
  hybrid       = _try_bm25 + _try_milvus + _rrf_fuse 直连组合（纯 hybrid 无图路）
  hybrid+graph = 全链 service.query()（graph 实体项 kind=graph 不计入 chunk 指标，如实过滤）
  +visual      = search_visual_by_image 图片集（正例自检索 + 未入库候选负例分离 → CLIP 判据）

诚实边界（A-16）：ground truth=维护者标注 16 条（取自已核实 chunk 内容，非第三方集）；
视觉为单范例库规模（自检索+负例分离），判据仅就混淆分离度；冷启动 warmup 一轮弃测。
用法（仓库根）：set -a; source .env; set +a; python scripts/eval_retrieval.py
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from kb.bm25 import bm25_search  # noqa: E402
from kb.eval_metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402
from knowledge.service import GraphRAGService, _rrf_fuse  # noqa: E402

DATASET = Path(__file__).resolve().parent / "eval_dataset.json"
REPORT = _ROOT / "docs" / "reviews" / "2026-11-26-检索评测报告-T177.md"
K = 5


def hit_ids(hits: list[dict]) -> list[str]:
    """hits → identity 串列表（过滤图实体项——chunk 级指标口径）。"""
    return [
        f"{h.get('doc_id')}:{h.get('location')}"
        for h in hits
        if h.get("kind") != "graph"
    ]


def run_vector(svc: GraphRAGService, q: str) -> list[dict]:
    out = svc._try_milvus(q)
    return out[2][:10] if out else []


def run_bm25(svc: GraphRAGService, q: str) -> list[dict]:  # noqa: ARG001
    return bm25_search(q, top_k=10) or []


def run_hybrid(svc: GraphRAGService, q: str) -> list[dict]:
    b = svc._try_bm25(q)
    v = svc._try_milvus(q)
    if b is None and v is None:
        return []
    fused = _rrf_fuse(b[2] if b else [], v[2] if v else [])
    return fused[:10]


def run_hybrid_graph(svc: GraphRAGService, q: str) -> list[dict]:
    r = svc.query(q)
    return r.hits[:10]


CONFIGS = [
    ("vector", run_vector),
    ("bm25", run_bm25),
    ("hybrid", run_hybrid),
    ("hybrid+graph", run_hybrid_graph),
]


def run_text_eval() -> tuple[dict, list[dict]]:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    svc = GraphRAGService(enable_kb_levels=True)
    svc.sdk = None  # SDK 级不在对照范围（如实）；同时避免 unscoped SDK 长等待
    svc.bm25_enabled = True

    # 冷启动 warmup（embeddings 首调延迟与 BM25 语料首载，弃测）
    run_hybrid_graph(svc, data["queries"][0]["query"])

    per_query: list[dict] = []
    summary: dict[str, dict[str, float]] = {
        name: {"recall": [], "mrr": [], "ndcg": [], "latency": []} for name, _ in CONFIGS
    }
    for qrec in data["queries"]:
        relevant = set(qrec["relevant"])
        row = {"id": qrec["id"], "query": qrec["query"]}
        for name, runner in CONFIGS:
            t0 = time.perf_counter()
            hits = runner(svc, qrec["query"])
            dt = (time.perf_counter() - t0) * 1000
            ids = hit_ids(hits)
            row[name] = {
                "recall@5": round(recall_at_k(ids, relevant, K), 3),
                "mrr": round(mrr(ids, relevant), 3),
                "ndcg@5": round(ndcg_at_k(ids, relevant, K), 3),
                "ms": round(dt, 1),
            }
            summary[name]["recall"].append(row[name]["recall@5"])
            summary[name]["mrr"].append(row[name]["mrr"])
            summary[name]["ndcg"].append(row[name]["ndcg@5"])
            summary[name]["latency"].append(dt)
        per_query.append(row)
        print(f"  [{qrec['id']}] " + " | ".join(
            f"{n}: r@5={row[n]['recall@5']:.2f} ndcg={row[n]['ndcg@5']:.2f} {row[n]['ms']:.0f}ms"
            for n, _ in CONFIGS
        ), flush=True)

    agg = {}
    for name, vals in summary.items():
        agg[name] = {
            "recall@5": round(statistics.mean(vals["recall"]), 3),
            "mrr": round(statistics.mean(vals["mrr"]), 3),
            "ndcg@5": round(statistics.mean(vals["ndcg"]), 3),
            "p50_ms": round(statistics.median(vals["latency"]), 1),
            "p95_ms": round(sorted(vals["latency"])[int(len(vals["latency"]) * 0.95) - 1], 1),
        }
    return agg, per_query


def run_visual_eval(data: dict) -> dict:
    """正例自检索 + 未入库候选负例分离 → CLIP 条件立项判据。"""
    from cad_engine.ref_visual import search_visual_by_image

    vis = data["visual"]
    import os

    pos_path = (vis["positive_image"]
                .replace("%TEMP%", os.environ.get("TEMP", tempfile.gettempdir())))
    png = Path(pos_path) if Path(pos_path).exists() else None
    if png is None:
        return {"ok": False, "reason": "正例查询图不存在（temp/t175_probe_views.png）"}

    rows = []
    for attempt in range(2):  # Qwen3-VL 瞬态超时重试一次
        hits = search_visual_by_image(png.read_bytes(), top_k=3)
        if hits:
            break
    if not hits:
        return {"ok": False, "reason": "caption 瞬态失败（重试后仍空）"}
    top_ref, (_rank, top_score, _cap) = next(iter(hits.items()))
    rows.append({"image": "towed views（已入库正例）", "top_ref": top_ref,
                 "score": round(top_score, 3),
                 "expected": vis["positive_ref"],
                 "correct": top_ref == vis["positive_ref"]})

    negatives = []
    for img in vis["negative_images"]:
        ip = _ROOT / img["path"]
        if not ip.exists():
            continue
        ih = search_visual_by_image(ip.read_bytes(), top_k=1)
        if ih:
            ref, (_r, score, cap) = next(iter(ih.items()))
            negatives.append({"image": img["label"], "top_ref": ref,
                              "score": round(score, 3), "caption_head": cap[:60]})
        else:
            negatives.append({"image": img["label"], "top_ref": "(caption 失败)",
                              "score": None, "caption_head": ""})
    pos_score = rows[0]["score"]
    neg_scores = [n["score"] for n in negatives if n["score"] is not None]
    separation = round(pos_score - max(neg_scores), 3) if neg_scores else None
    clip_verdict = (
        "caption-then-embed 足够（分离度 Δ≥0.1，无跨类型混淆）" if separation is not None and separation >= 0.1
        else "CLIP 联合空间立项证据（分离度 Δ<0.1 或负例反超——像素级通道缺失导致混淆）"
        if separation is not None
        else "判据不可产出（负例 caption 全部瞬态失败，A-16 如实）"
    )
    return {"ok": True, "rows": rows, "negatives": negatives,
            "separation": separation, "clip_verdict": clip_verdict}


def main() -> int:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    print("[eval] 文本查询 16 条 × 4 配置（vector/bm25/hybrid/hybrid+graph）…", flush=True)
    agg, per_query = run_text_eval()
    print("[eval] 视觉查询（正例+3 负例分离）…", flush=True)
    visual = run_visual_eval(data)

    lines = [
        "# 检索评测报告 · T17.7（五配置对照；毕设实验数据）",
        "",
        f"- 评测集：维护者标注 16 条文本查询（relevant=doc_id:段落N，取自已核实 chunk 内容，非第三方集——A-16 如实）",
        "- 配置：vector（Milvus 单路）/ bm25（稀疏单路）/ hybrid（双路 RRF）/ hybrid+graph（全链三路）；"
        "SDK 级不在对照范围（sdk=None 确定性跳过）；graph 实体项不计入 chunk 指标",
        "- 视觉：正例自检索 + 3 未入库候选负例分离（单范例库规模局限如实）",
        "",
        "## 汇总（均值，n=16）",
        "",
        "| 配置 | Recall@5 | MRR | nDCG@5 | P50 (ms) | P95 (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("vector", "bm25", "hybrid", "hybrid+graph"):
        a = agg[name]
        lines.append(f"| {name} | {a['recall@5']} | {a['mrr']} | {a['ndcg@5']} | {a['p50_ms']} | {a['p95_ms']} |")
    lines += ["", "## 逐查询明细", "",
              "| id | vector r@5/mrr | bm25 r@5/mrr | hybrid r@5/mrr | hybrid+graph r@5/mrr | hybrid ms |", "",
              "|---|---|---|---|---|---|"]
    for row in per_query:
        lines.append(
            f"| {row['id']} | {row['vector']['recall@5']}/{row['vector']['mrr']} "
            f"| {row['bm25']['recall@5']}/{row['bm25']['mrr']} "
            f"| {row['hybrid']['recall@5']}/{row['hybrid']['mrr']} "
            f"| {row['hybrid+graph']['recall@5']}/{row['hybrid+graph']['mrr']} "
            f"| {row['hybrid']['ms']} |"
        )
    lines += ["", "## 视觉通道与 CLIP 条件立项判据", ""]
    if visual.get("ok"):
        for r in visual["rows"]:
            lines.append(f"- 正例 {r['image']} → top={r['top_ref']} score={r['score']} "
                         f"(correct={r['correct']})")
        for n in visual["negatives"]:
            lines.append(f"- 负例 {n['image']} → top={n['top_ref']} score={n['score']} "
                         f"caption={n['caption_head']}…")
        lines.append(f"- **分离度 Δ = {visual['separation']}**")
        lines.append(f"- **判据：{visual['clip_verdict']}**")
    else:
        lines.append(f"- 视觉评测未完成：{visual.get('reason')}（A-16 如实）")
    lines += ["", "> 局限如实：维护者标注（非第三方）；单范例库视觉规模；graph 实体项不计入 chunk 指标；",
              "> SDK 级不在对照范围。报告由 scripts/eval_retrieval.py 生成，可复跑。"]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval] 报告: {REPORT}")
    print(json.dumps({"summary": agg, "visual_separation": visual.get("separation"),
                      "clip_verdict": visual.get("clip_verdict")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
