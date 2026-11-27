# -*- coding: utf-8 -*-
"""T16.2 B2 选例实验——检索配置矩阵 + 视觉通道重评（ADR-018 决策 2 判据核算）。

四配置（lexical / lexical+weak / fusion / fusion+weak）× 冻结文本集 → 命中/错域/净增/weak 精度；
视觉段 = 3 正例（MinIO 现取 views）+ 2 负例（未入库类型本地图）→ top-1 正确性 + Δ 分离度。
判据（ADR-018 决策 2 事先注册，未经事后放宽）：
  向量：A=错域注入 0 ∧ B=fusion+weak 相对 lexical+weak 正例净增命中 ≥3 → REF_VECTOR_ENABLED 默认开
  ALLOW_WEAK：weak 注入精度 ≥90% → 默认开
  视觉：3 正例 top-1 全中 ∧ Δ≥0.1 → REF_VISUAL_ENABLED 默认开
报告落 docs/reviews/2026-11-27-范例选例实验报告-T162.md（生成对照段由 eval_refgain.py 跑后追加）。

用法（仓库根）：set -a; source .env; set +a && python scripts/eval_refsel.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

import app_config  # noqa: E402
from cad_engine.reference import CodeSearchService  # noqa: E402
from storage import object_store, ref_store  # noqa: E402

DATASET = Path(__file__).resolve().parent / "eval_refsel_dataset.json"
REPORT = _ROOT / "docs" / "reviews" / "2026-11-27-范例选例实验报告-T162.md"

# (名称, REF_VECTOR_ENABLED, REF_MATCH_ALLOW_WEAK)——REF_SOURCE 固定 .env（db）
CONFIGS = [
    ("lexical",     False, False),
    ("lexical+weak", False, True),
    ("fusion",      True,  False),
    ("fusion+weak", True,  True),
]


def _run_matrix(entries: list[dict]) -> list[dict]:
    rows = []
    for name, vec, weak in CONFIGS:
        app_config.REF_VECTOR_ENABLED = vec
        app_config.REF_MATCH_ALLOW_WEAK = weak
        svc = CodeSearchService()
        per = []
        for e in entries:
            t0 = time.perf_counter()
            content, match = svc.select_reference(e["doc"])
            dt = round((time.perf_counter() - t0) * 1000, 1)
            hit = (match or {}).get("ref_id")
            grade = (match or {}).get("grade")
            if e["expected_ref_id"] is None:
                correct = match is None  # 负例：未注入=正确
            else:
                correct = hit == e["expected_ref_id"]
            per.append({
                "id": e["id"], "kind": e["kind"], "expected": e["expected_ref_id"],
                "hit": hit, "grade": grade, "score": (match or {}).get("score"),
                "reason": svc.last_reason, "correct": correct,
                "ms": dt, "content_chars": len(content),
            })
        rows.append({"config": name, "vector": vec, "allow_weak": weak, "per": per})
        print(f"[matrix] {name}: 正确 {sum(1 for p in per if p['correct'])}/{len(per)}")
    # 恢复默认（防进程内残留影响视觉段——视觉段不走此开关，惯例性恢复）
    app_config.REF_VECTOR_ENABLED = False
    app_config.REF_MATCH_ALLOW_WEAK = False
    return rows


def _metrics(matrix: list[dict]) -> dict:
    out: dict = {}
    for r in matrix:
        per = r["per"]
        pos = [p for p in per if p["kind"].startswith("pos")]
        neg = [p for p in per if p["kind"].startswith("neg")]
        weak_inj = [p for p in per if p.get("grade") == "weak_keyword"]
        out[r["config"]] = {
            "pos_correct": sum(1 for p in pos if p["correct"]),
            "pos_total": len(pos),
            "neg_injected": sum(1 for p in neg if not p["correct"]),  # 负例被注入=错域
            "weak_injected": len(weak_inj),
            "weak_correct": sum(1 for p in weak_inj if p["correct"]),
        }
    # 判据 B：fusion+weak 相对 lexical+weak 的正例正确净增
    lw = next(r for r in matrix if r["config"] == "lexical+weak")["per"]
    fw = next(r for r in matrix if r["config"] == "fusion+weak")["per"]
    lmap = {p["id"]: p["correct"] for p in lw}
    gain = [p["id"] for p in fw if p["kind"].startswith("pos") and p["correct"] and not lmap.get(p["id"])]
    out["gain_ids"] = gain
    out["gain_count"] = len(gain)
    return out


def _run_visual(vis_cfg: dict) -> dict:
    from cad_engine.ref_visual import search_visual_by_image
    from cad_engine.render import views_object_key

    rows = {r["ref_id"]: r for r in ref_store.get_active_rows()}
    pos = []
    for rid in vis_cfg["positive_ref_ids"]:
        row = rows.get(rid)
        if row is None:
            pos.append({"ref_id": rid, "skip": "active 行不存在"})
            continue
        png = object_store.download_bytes(row["bucket"], views_object_key(rid, row["version"]))
        hits = search_visual_by_image(png, top_k=3)
        top = min(hits.items(), key=lambda kv: kv[1][0]) if hits else None
        pos.append({
            "ref_id": rid,
            "top1": top[0] if top else None,
            "score": round(top[1][1], 3) if top else None,
            "correct": bool(top and top[0] == rid),
        })
        print(f"[visual] 正例 {rid}: top1={pos[-1]['top1']} score={pos[-1]['score']}")
    negs = []
    for img in vis_cfg["negative_images"]:
        p = _ROOT / img["path"]
        if not p.exists():
            negs.append({"label": img["label"], "skip": "文件不存在"})
            continue
        hits = search_visual_by_image(p.read_bytes(), top_k=3)
        top = min(hits.items(), key=lambda kv: kv[1][0]) if hits else None
        negs.append({
            "label": img["label"],
            "top1": top[0] if top else None,
            "score": round(top[1][1], 3) if top else None,
        })
        print(f"[visual] 负例 {img['label']}: top1={negs[-1]['top1']} score={negs[-1]['score']}")
    pos_scores = [p["score"] for p in pos if p.get("score") is not None]
    neg_scores = [n["score"] for n in negs if n.get("score") is not None]
    delta = round(min(pos_scores) - max(neg_scores), 3) if pos_scores and neg_scores else None
    return {"positives": pos, "negatives": negs, "delta": delta,
            "all_top1_correct": all(p.get("correct") for p in pos) and len(pos) == len(vis_cfg["positive_ref_ids"])}


def _write_report(data: dict, matrix: list[dict], metrics: dict, visual: dict) -> None:
    lines = [
        "# 范例选例实验报告 · T16.2（B2；ADR-018 决策 2 判据核算；毕设实验数据）",
        "",
        f"- 评测集：冻结集 scripts/eval_refsel_dataset.json（AI 标注 11 条文本+3 生成文档+视觉正负例，非第三方集——A-16 如实）",
        f"- 库快照：{data.get('library_snapshot', '')}",
        "- 配置矩阵：lexical / lexical+weak / fusion / fusion+weak（REF_SOURCE=db；进程内 setattr 切换）",
        "",
        "## 检索矩阵逐条",
        "",
        "| id | kind | 期望 | " + " | ".join(f"{c[0]} 命中/grade" for c in CONFIGS) + " |",
        "|---|---|---|" + "---|" * 4,
    ]
    per_id = {p["id"]: p for r in matrix for p in r["per"]}
    # 按配置列展开
    for e in data["text"]:
        cells = []
        for r in matrix:
            p = next(x for x in r["per"] if x["id"] == e["id"])
            mark = "✓" if p["correct"] else "✗"
            grade = p.get("grade") or "-"
            cells.append(f"{p.get('hit') or p.get('reason') or '-'} / {grade} {mark}")
        exp = e["expected_ref_id"] or "（不注入）"
        lines.append(f"| {e['id']} | {e['kind']} | {exp} | " + " | ".join(cells) + " |")
    lines += ["", "## 指标与判据核算（检索侧）", ""]
    lines.append("| 配置 | 正例正确 | 错域注入 | weak 注入 | weak 正确 |")
    lines.append("|---|---|---|---|---|")
    for name in [c[0] for c in CONFIGS]:
        m = metrics[name]
        lines.append(f"| {name} | {m['pos_correct']}/{m['pos_total']} | {m['neg_injected']} | {m['weak_injected']} | {m['weak_correct']} |")
    lines += [
        "",
        f"- **判据 A（错域注入 0）**：lexical={metrics['lexical']['neg_injected']}、lexical+weak={metrics['lexical+weak']['neg_injected']}、"
        f"fusion={metrics['fusion']['neg_injected']}、fusion+weak={metrics['fusion+weak']['neg_injected']}（默认关 weak 的两配置为主口径）",
        f"- **判据 B（fusion+weak 相对 lexical+weak 正例净增 ≥3）**：净增 {metrics['gain_count']} 条 {metrics['gain_ids']}",
        f"- **ALLOW_WEAK 门（weak 注入精度 ≥90%）**：weak 正确 {metrics['fusion+weak']['weak_correct']}/{max(metrics['fusion+weak']['weak_injected'], 1)}"
        f"（fusion+weak 口径；lexical+weak={metrics['lexical+weak']['weak_correct']}/{max(metrics['lexical+weak']['weak_injected'], 1)}）",
        "",
        "## 视觉通道重评（3 正例+2 负例；ADR-018 决策 2 判据）",
        "",
        "| 类别 | 查询 | top-1 | score | 正确 |",
        "|---|---|---|---|---|",
    ]
    for p in visual["positives"]:
        lines.append(f"| 正例 | {p['ref_id']}（MinIO views 现取） | {p.get('top1') or '-'} | {p.get('score')} | {'✓' if p.get('correct') else '✗'} |")
    for n in visual["negatives"]:
        if n.get("skip"):
            lines.append(f"| 负例 | {n['label']} | skip（{n['skip']}） | - | - |")
        else:
            lines.append(f"| 负例 | {n['label']} | {n.get('top1') or '-'} | {n.get('score')} | - |")
    lines += [
        "",
        f"- **Δ 分离度 = {visual['delta']}**（=min(正例 top1)−max(负例 top1)；T17.7 单正例口径的多正例推广，如实注明）",
        f"- **top-1 全中 = {visual['all_top1_correct']}**",
        "- 判据：3 正例 top-1 全中 ∧ Δ≥0.1 → REF_VISUAL_ENABLED 默认开；否则维持关",
        "",
        "> 生成对照段（有/无范例 × 3 文档 × 2 发）由 scripts/eval_refgain.py 实跑后追加于本文末尾。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval] 报告（前半）: {REPORT}")


def main() -> int:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    print(f"[eval] 冻结集 {DATASET.name}：文本 {len(data['text'])} 条 / 生成文档 {len(data['gen_docs'])} 份")
    matrix = _run_matrix(data["text"])
    metrics = _metrics(matrix)
    visual = _run_visual(data["visual"])
    _write_report(data, matrix, metrics, visual)
    summary = {"metrics": metrics, "visual_delta": visual["delta"],
               "visual_all_top1_correct": visual["all_top1_correct"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    (REPORT.parent / "t162-refsel-result.json").write_text(
        json.dumps({"matrix": matrix, "visual": visual, "metrics": metrics},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
