# -*- coding: utf-8 -*-
"""STEP 三视图渲染（T17.5 渲染服务化，PHASE-017；契约=ADR-016 §5）。

母本=T16.4 harness scripts/gen_ref_candidates.py --render（2026-11-22 候选可视化裁决包）；
沉淀为生产模块供范例 confirm 链调用。**子进程执行**（convert_worker/geometry_inspect 同款
纪律：OCCT/重镶嵌不进 API 进程），固定渲染脚本字符串（非用户代码）——cadquery importStep
→ tessellate 2.0 → matplotlib Agg Poly3DCollection 面-normal 明暗，单 PNG 三子图
（iso/side/top）。返回契约 {"ok": bool, "png": bytes | None, "error": str}——失败 ok=False
+error 如实（A-16），不抛不阻塞入库链。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("sonarcad.cad.render")

TIME_LIMIT_S = 180

# 固定渲染体（母本=gen_ref_candidates._RENDER_CODE；argv: step_path out_png stage_log）
# 实测教训（2026-11-26，towed twin 真模型 diag≈84m/48 元复合体）：
# ① 整复合体 tessellate(2.0) 产海量三角形 + matplotlib 逐面绘制 → >600s 病理卡死；
# ② 整复合体单次 tessellate 自身即卡死（OCCT BRepMesh 对巨复合体不可控，>1200s）——
#   逐 solid 独立剖分（每 solid 粗容差 sd*0.004 + 独立面帽 3000）使最坏情形有界，
#   全元素保留稀疏壳；绘制面全局抽稀到 30k（caption 只需轮廓/组成辨识）
_RENDER_CODE = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import cadquery as cq

step_path, out_png, stage_log = sys.argv[1], sys.argv[2], sys.argv[3]
t0 = __import__("time").time()
def log(msg):
    line = f"{msg} @" + str(__import__("time").time() - t0) + "s" + chr(10)
    with open(stage_log, "a", encoding="utf-8") as f:
        f.write(line)

step_path, out_png = sys.argv[1], sys.argv[2]
shape = cq.importers.importStep(step_path).val()
solids = shape.Solids() or [shape]
log(f"solids={len(solids)}")
V_parts, F_parts = [], []
offset = 0
for i, s in enumerate(solids):
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
log(f"tessellate per-solid faces={len(F)}")
MAX_FACES = 30000
if len(F) > MAX_FACES:
    stride = len(F) // MAX_FACES + 1
    F = F[::stride]
tri = V[F]
n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
norm = np.linalg.norm(n, axis=1, keepdims=True)
norm[norm == 0] = 1.0
n = n / norm
light = np.array([0.4, 0.35, 0.85])
light = light / np.linalg.norm(light)
intensity = 0.35 + 0.65 * np.clip(n @ light, 0.0, 1.0)
base = np.array([0.39, 0.58, 0.93])
colors = np.clip(intensity[:, None] * base[None, :], 0.0, 1.0)

# 逐轴百分位裁剪（实测：display 远端副本把 bbox 撑到 84m，主体只占几像素——
# 用顶点 0.5~99.5 百分位范围 +5% 边距，让主体占满视场）
lo = np.percentile(V, 0.5, axis=0)
hi = np.percentile(V, 99.5, axis=0)
margin = (hi - lo) * 0.05 + 1e-6
lo = lo - margin
hi = hi + margin
center = (lo + hi) / 2.0
span = float(np.max(hi - lo)) or 1.0

views = [(22, -60, "iso"), (0, -90, "side"), (90, -90, "top")]
fig = plt.figure(figsize=(15, 5))
for k, (elev, azim, label) in enumerate(views, 1):
    ax = fig.add_subplot(1, 3, k, projection="3d")
    ax.add_collection3d(Poly3DCollection(tri, facecolors=colors, edgecolors="none"))
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(label)
    ax.axis("off")
plt.tight_layout()
plt.savefig(out_png, dpi=110)
print("rendered", out_png)
'''


def render_step_views(step_path: str | Path) -> dict:
    """STEP → 组合三视图 PNG。返回 {"ok": bool, "png": bytes|None, "error": str}。"""
    with tempfile.TemporaryDirectory(prefix="refrender_") as td:
        out_png = Path(td) / "views.png"
        stage_log = Path(td) / "stages.log"
        stage_log.write_text("", encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _RENDER_CODE, str(step_path), str(out_png), str(stage_log)],
                capture_output=True, text=True, timeout=TIME_LIMIT_S,
            )
            if proc.returncode != 0 or "rendered" not in (proc.stdout or ""):
                err = (proc.stderr or proc.stdout or "")[-300:]
                logger.warning("render 失败: %s | stages: %s", err,
                               stage_log.read_text(encoding="utf-8")[-200:])
                return {"ok": False, "png": None, "error": err}
        except subprocess.TimeoutExpired:
            return {"ok": False, "png": None, "error": f"render timeout (>{TIME_LIMIT_S}s)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "png": None, "error": f"{type(exc).__name__}: {exc}"}
        if not out_png.exists():
            return {"ok": False, "png": None, "error": "render 子进程未产出 PNG"}
        return {"ok": True, "png": out_png.read_bytes(), "error": ""}


def views_object_key(ref_id: str, version) -> str:
    """MinIO ref-scripts 桶内三视图对象 key（{ref_id}/v{version}/views/views.png）。"""
    return f"{ref_id}/v{version}/views/views.png"
