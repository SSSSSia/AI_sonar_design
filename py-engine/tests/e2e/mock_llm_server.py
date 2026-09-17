# -*- coding: utf-8 -*-
"""本地 Mock LLM 供应商（T1.7 e2e 基础设施，OpenAI 兼容 /chat/completions）。

用途：LLM_API_KEY 未配置真实供应商时，让全链路 e2e（需求→IR1→IR2→IR3→STEP）
可以跑通**除 LLM 创作外的全部真实环节**（检索/求解/沙箱/MinIO/编排均为真实执行）。
注册进 llm_providers 表（priority 最高）即被 D-12 热切换自动采用；删除该行即回到真实供应商。

路由：按提示词特征返回脚本化响应（A1 规划 JSON / A2 参数 JSON / A2 设计文档 / A3 CadQuery 脚本）。
启动：python -m tests.e2e.mock_llm_server  （默认 127.0.0.1:8091）
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request

app = FastAPI(title="sonarcad-mock-llm")

# A1 规划（含 4~8 条子查询，契约要求）
PLAN_JSON = json.dumps({
    "objectives": [
        {"dimension": "阵元数", "value": 16, "explicit": True},
        {"dimension": "阵元间距", "value": 1.5, "explicit": True},
        {"dimension": "工作深度", "value": 300, "explicit": True},
        {"dimension": "频段", "value": None, "explicit": False},
    ],
    "missing_items": ["工作频段未明确", "拖曳航速未明确"],
    "sub_queries": [
        "拖曳线列阵声呐系统总体组成与结构布局",
        "声阵段各部件精确尺寸参数（外径内径长度壁厚）",
        "水听器内部元器件规格数量间距",
        "各部件装配关系与顺序",
        "材料与工艺约束",
        "干端/母船端设备规格与空间布局",
    ],
}, ensure_ascii=False)

# A2 参数提取（1 retrieved 案例 + 1 proposed 提议）
EXTRACT_JSON = json.dumps([
    {"name": "声阵段外径", "value": 27, "unit": "mm", "source": "retrieved",
     "rationale": "检索知识硬约束：声阵段外径不超过27mm"},
    {"name": "声阵段壁厚", "value": 3, "unit": "mm", "source": "retrieved",
     "rationale": "检索知识部件参数：壁厚3mm"},
    {"name": "标准阵元间距", "value": 1.5, "unit": "m", "source": "retrieved",
     "rationale": "检索知识硬约束：阵元间距1500mm±10mm"},
    {"name": "声阵段总长", "value": 24, "unit": "m", "source": "proposed",
     "rationale": "16 阵元 × 标准间距 1.5m 推得约 24m，处于检索知识标准参数区间内"},
], ensure_ascii=False)

# A2 设计文档（Markdown，部件明细驱动 A3）
DESIGN_DOC = """# 16 阵元拖曳线列阵声呐 CAD 设计文档

## 1. 项目概述
被动拖曳线列阵声呐，16 个水听器阵元，最大工作深度 300m，Z 轴为主轴。

## 2. 系统总体参数
- 阵元数目：16
- 阵元间距：1.5 m
- 最大工作深度：300 m
- 指向性指数：DI ≈ 10lg(16) ≈ 12 dB（公式计算）

## 3. 部件明细表

| 序号 | 部件名称 | Z起点(mm) | Z终点(mm) | 长度(mm) | 外径(mm) | 类型 | 弯曲振幅(mm) |
|------|----------|-----------|-----------|----------|----------|------|-------------|
| 1 | 前导缆（刚性） | 0 | 2000 | 2000 | 20 | 实心圆柱 | - |
| 2 | 声阵段（刚性薄壁管） | 2000 | 26000 | 24000 | 27 | 薄壁管 | - |
| 3 | 尾部柔性段（柔性） | 26000 | 28000 | 2000 | 20 | 柔性管 | ±400 |

## 4. 部件详细参数

### 4.1 前导缆
- **类型**: 实心圆柱
- **主体几何**: 外径 20 mm，长度 2000 mm
- **Z轴位置**: 0 ~ 2000 mm

### 4.2 声阵段
- **类型**: 薄壁管（外径 27 mm，壁厚 3 mm）
- **内部元器件**: 16 个水听器，间距 1500 mm，段内居中分布
- **Z轴位置**: 2000 ~ 26000 mm

### 4.3 尾部柔性段
- **类型**: 柔性管，弯曲振幅 ±400 mm（视觉自适应值）
- **Z轴位置**: 26000 ~ 28000 mm

## 5. 装配说明
沿 Z 轴正方向顺序装配：前导缆 → 声阵段 → 尾部柔性段；柔性段用样条弯曲建模。

## 6. 渲染配色方案
- 前导缆: Color(0.55, 0.55, 0.55, 0.9)
- 声阵段: Color(0.2, 0.45, 0.8, 0.5)
- 尾部柔性段: Color(0.1, 0.7, 0.3, 0.8)

## 7. 设计约束
### 硬约束（任何方案不可违反）
- 声阵段外径不超过 27 mm
- 阵元间距 1500mm±10mm
### 派生约束（本次方案计算结果）
- 有效波束成形频率上限 f_beam ≤ 1500/(2×1.5) = 500 Hz

## 8. 输出要求
- 文件格式: STEP, 坐标系: Z轴为主轴
"""

# A3 CadQuery 脚本（真实可执行：3 段装配 + sin² 弯曲柔性段 + 内部阵元 + STEP 导出）
CAD_SCRIPT = """import math, os
import cadquery as cq
from cadquery import Color

# ========== 参数（来自设计文档，单位 mm）==========
N_ELEMENTS = 16
ELEMENT_SPACING = 1500.0
ARRAY_OUTER_R = 13.5
ARRAY_INNER_R = 10.5
SECTION_LEAD = 2000.0
SECTION_ARRAY = 24000.0
SECTION_TAIL = 2000.0
TAIL_AMPLITUDE = 400.0  # 视觉自适应振幅

C_LEAD = Color(0.55, 0.55, 0.55, 0.9)
C_ARRAY = Color(0.2, 0.45, 0.8, 0.5)
C_TAIL = Color(0.1, 0.7, 0.3, 0.8)

def get_spline_pts(length, amplitude, z_start=0):
    pts = []
    for i in range(11):
        pct = i / 10.0
        z = z_start + length * pct
        x = amplitude * (math.sin(math.pi * pct) ** 2)
        pts.append((x, z))
    return pts

def build_lead(z_start):
    part = cq.Workplane("XY").circle(10.0).extrude(SECTION_LEAD)
    return (part.translate((0, 0, z_start)), z_start + SECTION_LEAD)

def build_array(z_start):
    outer = cq.Workplane("XY").circle(ARRAY_OUTER_R).circle(ARRAY_INNER_R).extrude(SECTION_ARRAY)
    group = []
    # 内部水听器：段内居中、等间距
    margin = ELEMENT_SPACING / 2.0
    count = int((SECTION_ARRAY - 2 * margin) // ELEMENT_SPACING) + 1
    for i in range(min(count, N_ELEMENTS)):
        z_local = margin + i * ELEMENT_SPACING
        elem = cq.Workplane("XY").workplane(offset=z_local).circle(6.0).extrude(60.0)
        group.append((elem.translate((0, 0, z_start)), C_TAIL, f"hydrophone_{i+1}"))
    moved = [(p.translate((0, 0, z_start)), c, n) for p, c, n in
             [(outer, C_ARRAY, "array_section")]] + group
    return moved, z_start + SECTION_ARRAY

def build_tail(z_start):
    pts = get_spline_pts(SECTION_TAIL, -TAIL_AMPLITUDE, 0)
    path = cq.Workplane("XZ").spline(pts)
    outer = cq.Workplane("XY").circle(10.0).sweep(path)
    inner = cq.Workplane("XY").circle(7.0).sweep(path)
    tube = outer.cut(inner)
    return (tube.translate((0, 0, z_start)), z_start + SECTION_TAIL)

# ========== 主装配 ==========
assy = cq.Assembly()
z_cursor = 0

lead, z_cursor = build_lead(z_cursor)
assy.add(lead, color=C_LEAD, name="lead_cable")

group, z_cursor = build_array(z_cursor)
for p, c, n in group:
    assy.add(p, color=c, name=n)

tail, z_cursor = build_tail(z_cursor)
assy.add(tail, color=C_TAIL, name="tail_flex")

# ========== 导出 ==========
os.makedirs(OUTPUT_DIR, exist_ok=True)
assy.save(os.path.join(OUTPUT_DIR, "sonar_model.step"), "STEP")
"""


def _content_of(body: dict) -> str:
    msgs = body.get("messages", [])
    system = next((m.get("content", "") for m in msgs if m.get("role") == "system"), "")
    user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
    return system + "\n" + user


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    text = _content_of(body)

    if "请修复代码" in text:  # A3 修复循环：直接返回工作脚本
        content = CAD_SCRIPT
    elif "精通 CadQuery" in text:  # A3 代码生成
        content = CAD_SCRIPT
    elif "需求解析专家" in text:  # A1 规划
        content = PLAN_JSON
    elif "参数提取专家" in text:  # A2 参数提取
        content = EXTRACT_JSON
    elif "设计文档" in text:  # A2 文档整合
        content = DESIGN_DOC
    else:
        content = "{}"

    return {
        "id": "mock-completion",
        "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "mock-llm"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8091, log_level="warning")
