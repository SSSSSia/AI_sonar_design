# -*- coding: utf-8 -*-
"""T17.8 CLIP 联合空间离线对照实验（ADR-018 决策 3；限本地环境，零生产改动）。

三方通道对照（同查询集、同库类型）：
  CLIP   = SigLIP 2 base 图像塔 embed，9 库图（3 范例×3 单视图）内存余弦（零新基建）
  caption= 生产链 describe_images(单视图 prompt 变体)→embed_texts→Milvus ref_visuals（3 条整图 caption）
  fusion = 两通道类型名次 RRF(k=60)（kb 同款公式，进程内）

协议（留一视图）：查询=范例 X 的单视图 v；CLIP 候选=9 库图中除「X 的 v」外 8 张（类型 top-1=最高分图
是否属于类型 X）；caption 候选=3 类型（生产形态）。正例查询 9 张+负例查询 6 张（未入库拖鱼 v2/v3
单视图，期望低分）。判据（ADR-018 事先注册）：CLIP top-1 严格高于 caption ∧ CLIP Δ≥0.1 ∧ 离线
（天然满足）→ 另立集成任务；否则不集成，方向关闭。

用法（仓库根，底座在跑——caption/embedding/Milvus 走生产链）：
    set -a; source .env; set +a
    python scripts/eval_clip.py
依赖：torch/transformers（离线实验依赖，不入 requirements——ADR-018 决策 3「不进 py 容器」）；
SigLIP 2 权重经 HF_HUB_ENDPOINT=https://hf-mirror.com 预下载（~/.cache，不入仓库）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "py-engine"))

from cad_engine import sandbox  # noqa: E402
from llm.vision import describe_images  # noqa: E402

OUT_DIR = _ROOT / "docs" / "reviews" / "t178-clips"
REPORT = _ROOT / "docs" / "reviews" / "2026-11-28-CLIP离线对照实验报告-T178.md"
CLIP_MODEL = "google/siglip2-base-patch16-224"
CLIP_FALLBACK = "google/siglip-base-patch16-224"
RRF_K = 60
VIEWS = [("iso", 22, -60), ("side", 0, -90), ("top", 90, -90)]

# 单视图渲染变体（照 cad_engine/render.py _RENDER_CODE 生产母本裁剪：逐 solid 自适应容差
# sd*0.004+面帽 3000+全局 30k 抽稀+百分位裁剪——T17.5 病理卡死修复策略照搬；单 subplot 无标题。
# 零生产改动——本字符串仅存在于本实验脚本）
_RENDER_SINGLE = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import cadquery as cq

step_path, out_png, elev, azim = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
shape = cq.importers.importStep(step_path).val()
solids = shape.Solids() or [shape]
V_parts, F_parts = [], []
offset = 0
for s in solids:
    sb = s.BoundingBox()
    sd = float((sb.xlen ** 2 + sb.ylen ** 2 + sb.zlen ** 2) ** 0.5) or 1.0
    v_s, t_s = s.tessellate(max(3.0, sd * 0.004))
    V_s = np.array([(v.x, v.y, v.z) for v in v_s])
    F_s = np.array(t_s) + offset
    offset += len(V_s)
    if len(F_s) > 3000:
        stride = len(F_s) // 3000 + 1
        F_s = F_s[::stride]
    V_parts.append(V_s)
    F_parts.append(F_s)
V = np.vstack(V_parts)
F = np.vstack(F_parts)
MAX_FACES = 30000
if len(F) > MAX_FACES:
    stride = len(F) // MAX_FACES + 1
    F = F[::stride]
tri = V[F]
n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
norm = np.linalg.norm(n, axis=1, keepdims=True)
norm[norm == 0] = 1.0
n = n / norm
light = np.array([0.4, 0.35, 0.85]); light = light / np.linalg.norm(light)
intensity = 0.35 + 0.65 * np.clip(n @ light, 0.0, 1.0)
base_c = np.array([0.39, 0.58, 0.93])
colors = np.clip(intensity[:, None] * base_c[None, :], 0.0, 1.0)
lo = np.percentile(V, 0.5, axis=0)
hi = np.percentile(V, 99.5, axis=0)
margin = (hi - lo) * 0.05 + 1e-6
lo, hi = lo - margin, hi + margin
span = float(np.max(hi - lo)) or 1.0
center = (lo + hi) / 2.0
fig = plt.figure(figsize=(6, 6))
ax = fig.add_subplot(1, 1, 1, projection="3d")
ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, edgecolors="none"))
ax.set_xlim(center[0] - span / 2, center[0] + span / 2)
ax.set_ylim(center[1] - span / 2, center[1] + span / 2)
ax.set_zlim(center[2] - span / 2, center[2] + span / 2)
ax.set_box_aspect((1, 1, 1))
ax.view_init(elev=elev, azim=azim)
ax.axis("off")
plt.tight_layout()
plt.savefig(out_png, dpi=110)
print("rendered", out_png)
'''

# 单视图 caption prompt（CAPTION_PROMPT 写的是「组合三视图」——单视图查询如实换变体，报告注明）
CAPTION_SINGLE_PROMPT = (
    "这是同一个声呐装备三维模型的一个视图。用中文描述该装备："
    "整体形态与安装形式、可见的主要组成部分、显著几何特征（圆柱/锥/板/阵列/环等）。"
    "只输出描述正文，不要客套或分析过程。"
)


# ---------------------------------------------------------------- 资产构造
def _render_single(step_file: Path, out_png: Path, elev: float, azim: float) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RENDER_SINGLE, str(step_file), str(out_png), str(elev), str(azim)],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "render 超时（>180s，强制终止）"}
    ok = proc.returncode == 0 and "rendered" in (proc.stdout or "")
    return {"ok": ok, "error": (proc.stderr or proc.stdout or "")[-300:] if not ok else ""}


def _replay_to_step(code: str) -> Path | None:
    replay = sandbox.run_in_subprocess(code, time_limit_s=300)
    if not replay["ok"] or not replay.get("step_files"):
        return None
    return Path(replay["step_files"][0])


def build_assets() -> dict:
    """库 9 图（3 范例×3 视图）+正例查询 9 图（同源留一视图）+负例查询 6 图（拖鱼 v2/v3）。"""
    from storage import object_store, ref_store

    lib_dir = OUT_DIR / "lib"
    neg_dir = OUT_DIR / "negative"
    for d in (lib_dir, neg_dir):
        d.mkdir(parents=True, exist_ok=True)
    assets: dict = {"lib": [], "neg": []}

    for row in ref_store.get_active_rows():
        content = object_store.download_text(row["bucket"], row["object_key"])
        step = _replay_to_step(content)
        if step is None:
            print(f"[asset] {row['ref_id']} 重放失败 → 跳过（如实）")
            continue
        for view, elev, azim in VIEWS:
            png = lib_dir / f"{row['ref_id']}-{view}.png"
            r = _render_single(step, png, elev, azim)
            assets["lib"].append({"ref_id": row["ref_id"], "view": view,
                                  "png": str(png), "ok": r["ok"]})
            print(f"[asset] lib {row['ref_id']}-{view}: {'ok' if r['ok'] else r['error'][:80]}")

    for cand in ("side_scan_towfish_v2.py", "side_scan_towfish_v3.py"):
        code = (_ROOT / "docs" / "reviews" / "t164-candidates" / cand).read_text(encoding="utf-8")
        step = _replay_to_step(code)
        if step is None:
            print(f"[asset] {cand} 重放失败 → 跳过（如实）")
            continue
        tag = cand.replace(".py", "").replace("side_scan_towfish_", "towfish")
        for view, elev, azim in VIEWS:
            png = neg_dir / f"{tag}-{view}.png"
            r = _render_single(step, png, elev, azim)
            assets["neg"].append({"ref_id": None, "label": f"{tag}-{view}",
                                  "png": str(png), "ok": r["ok"]})
            print(f"[asset] neg {tag}-{view}: {'ok' if r['ok'] else r['error'][:80]}")
    (OUT_DIR / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=1), encoding="utf-8")
    return assets


# ---------------------------------------------------------------- CLIP 通道
def run_clip_channel(assets: dict) -> dict:
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    lib = [a for a in assets["lib"] if a["ok"]]
    neg = [a for a in assets["neg"] if a["ok"]]
    model_name = CLIP_MODEL
    try:
        processor = AutoProcessor.from_pretrained(model_name, local_files_only=True)
        model = AutoModel.from_pretrained(model_name, local_files_only=True)
    except Exception as exc:
        print(f"[clip] {CLIP_MODEL} 不可得（{str(exc)[:80]}）→ 退一代 {CLIP_FALLBACK}（如实记录）")
        model_name = CLIP_FALLBACK
        processor = AutoProcessor.from_pretrained(model_name, local_files_only=True)
        model = AutoModel.from_pretrained(model_name, local_files_only=True)
    model.eval()

    def _embed(png_path: str) -> list[float]:
        img = Image.open(png_path).convert("RGB")
        inputs = processor(images=img, return_tensors="pt")
        with torch.no_grad():
            out = model.get_image_features(**inputs)
        feats_t = out.pooler_output if hasattr(out, "pooler_output") else out  # transformers 5.x 返回输出对象
        v = feats_t / feats_t.norm(dim=-1, keepdim=True)
        return v[0].tolist()

    lib_feats = [_embed(a["png"]) for a in lib]
    neg_feats = [_embed(a["png"]) for a in neg]

    def _per_type(q_feat, skip_self):
        per = {}
        for j, item in enumerate(lib):
            if skip_self and (item["ref_id"], item["view"]) == skip_self:
                continue
            s = sum(x * y for x, y in zip(q_feat, lib_feats[j]))  # 双方已归一化，点积=余弦
            per[item["ref_id"]] = max(per.get(item["ref_id"], -1.0), s)
        return per

    rows = []
    for i, q in enumerate(lib):
        per = _per_type(lib_feats[i], skip_self=(q["ref_id"], q["view"]))
        best = max(per.items(), key=lambda x: x[1])
        rows.append({"query": f"{q['ref_id']}-{q['view']}", "expect": q["ref_id"],
                     "top1_type": best[0], "score": round(best[1], 4),
                     "hit": best[0] == q["ref_id"], "channel": "clip",
                     "per_type": {k: round(v, 4) for k, v in per.items()}})
    for i, q in enumerate(neg):
        per = _per_type(neg_feats[i], skip_self=None)
        best = max(per.items(), key=lambda x: x[1])
        rows.append({"query": q["label"], "expect": None, "top1_type": best[0],
                     "score": round(best[1], 4), "hit": None, "channel": "clip",
                     "per_type": {k: round(v, 4) for k, v in per.items()}})
    n_pos = sum(1 for r in rows if r["expect"])
    print(f"[clip] model={model_name} 正例命中 {sum(1 for r in rows if r['hit'])}/{n_pos}")
    return {"rows": rows, "model": model_name}


# ---------------------------------------------------------------- caption 通道
def run_caption_channel(assets: dict) -> dict:
    from kb.embeddings import embed_texts
    from kb.vector_store import ChunkVectorStore
    import app_config

    store = ChunkVectorStore(collection=app_config.KB_REF_VISUAL_COLLECTION)
    rows = []
    queries = [a for a in assets["lib"] if a["ok"]] + [a for a in assets["neg"] if a["ok"]]
    for q in queries:
        expect = q.get("ref_id")
        label = f"{q['ref_id']}-{q['view']}" if expect else q["label"]
        png = Path(q["png"]).read_bytes()
        cap_txt = None
        for _ in range(2):  # Qwen3-VL 瞬态重试一次（eval_retrieval 同款）
            cap_txt = describe_images([png], CAPTION_SINGLE_PROMPT)
            if cap_txt:
                break
        if not cap_txt:
            rows.append({"query": label, "expect": expect, "top1_type": "(caption 失败)",
                         "score": None, "hit": None, "channel": "caption", "per_type": {}})
            print(f"[caption] {label}: caption 失败（重试后）")
            continue
        vecs = embed_texts([cap_txt])
        if not vecs:
            rows.append({"query": label, "expect": expect, "top1_type": "(embed 失败)",
                         "score": None, "hit": None, "channel": "caption", "per_type": {}})
            continue
        hits = store.search(vecs[0], top_k=3)
        per = {}
        for h in hits:
            per[h.get("doc_id")] = max(per.get(h.get("doc_id"), -1.0), float(h.get("score") or 0.0))
        best = max(per.items(), key=lambda x: x[1])
        hit = (best[0] == expect) if expect else None
        rows.append({"query": label, "expect": expect, "top1_type": best[0],
                     "score": round(best[1], 4), "hit": hit, "channel": "caption",
                     "per_type": {k: round(v, 4) for k, v in per.items()},
                     "caption": cap_txt[:60]})
        print(f"[caption] {label}: top1={best[0]} score={best[1]:.3f}")
    return {"rows": rows}


# ---------------------------------------------------------------- 融合通道
def run_fusion_channel(clip: dict, cap: dict) -> dict:
    cap_by_q = {r["query"]: r for r in cap["rows"] if r.get("per_type")}
    rows = []
    for r in clip["rows"]:
        if not r.get("expect"):
            continue
        p = cap_by_q.get(r["query"])
        if p is None:
            continue
        clip_ranks = {rid: i + 1 for i, (rid, _s) in
                      enumerate(sorted(r["per_type"].items(), key=lambda x: -x[1]))}
        cap_ranks = {rid: i + 1 for i, (rid, _s) in
                     enumerate(sorted(p["per_type"].items(), key=lambda x: -x[1]))}
        fused = {rid: 1 / (RRF_K + clip_ranks.get(rid, 99)) + 1 / (RRF_K + cap_ranks.get(rid, 99))
                 for rid in set(clip_ranks) | set(cap_ranks)}
        best = max(fused.items(), key=lambda x: x[1])
        rows.append({"query": r["query"], "expect": r["expect"], "top1_type": best[0],
                     "hit": best[0] == r["expect"], "score": round(best[1], 4), "channel": "fusion"})
    return {"rows": rows}


# ---------------------------------------------------------------- 指标与报告
def _metrics(rows: list[dict]) -> dict:
    pos = [r for r in rows if r.get("expect")]
    pos_scored = [r for r in pos if r.get("score") is not None]
    neg_scores = [r["score"] for r in rows if not r.get("expect") and r.get("score") is not None]
    hits = sum(1 for r in pos_scored if r.get("hit"))
    delta = None
    pos_scores = [r["score"] for r in pos_scored]
    if pos_scores and neg_scores:
        delta = round(min(pos_scores) - max(neg_scores), 4)
    return {"pos_total": len(pos), "pos_scored": len(pos_scored), "hits": hits,
            "hit_rate": round(hits / len(pos_scored), 3) if pos_scored else None,
            "delta": delta,
            "missed": [r["query"] for r in pos_scored if not r.get("hit")]}


def _write_report(clip: dict, cap: dict, fus: dict, m_clip: dict, m_cap: dict, m_fus: dict) -> None:
    cap_by_q = {r["query"]: r for r in cap["rows"]}
    fus_by_q = {r["query"]: r for r in fus["rows"]}
    lines = [
        "# CLIP 离线对照实验报告 · T17.8（caption/CLIP/融合三方；ADR-018 决策 3 集成判据；毕设实验数据）",
        "",
        "- 协议：留一视图——查询=范例 X 单视图 v，CLIP 候选=9 库图（3 范例×3 视图）中除「X 的 v」外 8 张；caption 候选=3 类型（生产 Milvus ref_visuals 整图 caption）；负例查询=未入库拖鱼 v2/v3 单视图 6 张",
        f"- CLIP 模型：{clip.get('model')}（图像塔图-图余弦，CPU fp32，本地离线；权重经 hf-mirror 预下载，不入仓库）",
        "- caption 口径：查询单视图 prompt 变体（CAPTION_PROMPT 原文为「组合三视图」——不对称如实注明）vs 库整图 caption；Qwen3-VL 瞬态重试一次",
        "",
        "## 三通道对照",
        "",
        "| 通道 | 正例类型 top-1 命中 | Δ（正例 top1 min − 负例 top1 max） | 未命中查询 |",
        "|---|---|---|---|",
        f"| CLIP | {m_clip['hits']}/{m_clip['pos_scored']}（{m_clip['hit_rate']}） | {m_clip['delta']} | {', '.join(m_clip['missed']) or '-'} |",
        f"| caption（生产链） | {m_cap['hits']}/{m_cap['pos_scored']}（{m_cap['hit_rate']}） | {m_cap['delta']} | {', '.join(m_cap['missed']) or '-'} |",
        f"| fusion（RRF k={RRF_K}） | {m_fus['hits']}/{m_fus['pos_scored']}（{m_fus['hit_rate']}） | {m_fus['delta']} | {', '.join(m_fus['missed']) or '-'} |",
        "",
        "## 逐查询明细",
        "",
        "| 查询 | 期望 | CLIP top1/score | caption top1/score | fusion top1 |",
        "|---|---|---|---|---|",
    ]
    all_q = [r["query"] for r in clip["rows"]] + [r["query"] for r in cap["rows"]
                                                  if r["query"] not in {c["query"] for c in clip["rows"]}]
    for q in all_q:
        c = next((r for r in clip["rows"] if r["query"] == q), None)
        p = cap_by_q.get(q)
        f = fus_by_q.get(q)
        expect = (c or p).get("expect")
        lines.append(
            f"| {q} | {expect or '（负例）'} "
            f"| {c['top1_type'] if c else '-'}/{c['score'] if c else '-'} "
            f"| {p['top1_type'] if p else '-'}/{p['score'] if p else '-'} "
            f"| {f['top1_type'] if f else '-'} |"
        )
    ok1 = (m_clip["hit_rate"] or 0) > (m_cap["hit_rate"] or 0)
    ok2 = m_clip["delta"] is not None and m_clip["delta"] >= 0.1
    lines += [
        "",
        "## 集成判据核算（ADR-018 决策 3 事先注册，未经事后放宽）",
        "",
        f"1. CLIP top-1 命中率严格高于 caption：{m_clip['hit_rate']} vs {m_cap['hit_rate']} → {'满足' if ok1 else '不满足'}",
        f"2. CLIP Δ≥0.1：{m_clip['delta']} → {'满足' if ok2 else '不满足'}",
        "3. 不向 py 容器引入 torch：满足（本实验即离线本地；任何集成方案须走 API 或独立进程）",
        "",
    ]
    if ok1 and ok2:
        lines.append("**裁决：三条件同时满足 → CLIP 生产集成另立任务（集成形态须走 API/独立进程，不进 py 容器）**")
    else:
        lines.append(
            "**裁决：集成判据不满足 → 不集成，方向关闭（结果归档）**——如实记录不满足项；"
            "caption 通道维持生产默认关（T16.2 裁决不变）。"
        )
    lines += [
        "",
        "> 局限如实：单视图 prompt 变体 vs 库整图 caption 不对称；库侧 CLIP=单视图图集 vs caption=整图 caption（协议差异如实）；SigLIP 对渲染线稿的域差是实验本体问题；3 类型小库（9 库图 8 候选）；单次运行（caption VLM 非确定性）；负例仅拖鱼一族；CPU fp32 推理无量化。",
        "> 报告由 scripts/eval_clip.py 生成，逐查询矩阵 JSON 在 docs/reviews/t178-clips/，可复跑。",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval] 报告: {REPORT}")


def main() -> int:
    assets = build_assets()
    n_ok = sum(1 for a in assets["lib"] if a["ok"])
    n_neg = sum(1 for a in assets["neg"] if a["ok"])
    print(f"[eval] 库图 {n_ok}/9、负例 {n_neg}/6")
    if n_ok < 9 or n_neg < 6:
        print("[FATAL] 资产不全（重放/渲染失败）——如实停")
        return 1

    clip = run_clip_channel(assets)
    cap = run_caption_channel(assets)
    fus = run_fusion_channel(clip, cap)
    m_clip, m_cap, m_fus = _metrics(clip["rows"]), _metrics(cap["rows"]), _metrics(fus["rows"])
    _write_report(clip, cap, fus, m_clip, m_cap, m_fus)
    (OUT_DIR / "results.json").write_text(json.dumps(
        {"clip": clip, "caption": cap, "fusion": fus,
         "metrics": {"clip": m_clip, "caption": m_cap, "fusion": m_fus}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"clip": m_clip, "caption": m_cap, "fusion": m_fus},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
