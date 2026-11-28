# -*- coding: utf-8 -*-
"""侧扫声呐拖鱼参数化配方（T19.1 首批，PHASE-019，契约=ADR-019 决策 1）。

来源=v3.1 贯穿式配方 gold 骨架升格（scripts/gen_ref_candidates.py TYPE_SPECS towfish，
2026-11-28 本地验证 solids=1 / bbox 1450×300×200，提交 c4b8423）。配方=参数化构建函数，
不是 prompt 注入用的成品脚本：领域约定（贯穿式构造 / workplane offset 累计相对量语义 /
翼板横穿自然体积重叠 / 拖曳环嵌尾锥）固化在确定性代码里——LLM 退场，连通 by construction。

构造顺序（骨架 gold，禁改）：
  nose  首锥  YZ 面 offset=L_HULL（R_HULL）→ +L_NOSE（R_NOSE_TIP）loft——小端朝 +X 外
  cyl   主体  YZ 面 circle(R_HULL).extrude(L_HULL)，X∈[0, L_HULL]
  tail  尾锥  offset=-L_TAIL（R_TAIL_TIP）→ +L_TAIL（R_HULL）loft——小端朝 -X 外
  fin   翼板  XY 面 box(C_FIN, 2·SPAN_FIN, T_FIN) 平移主体中部——横穿自然体积重叠
  ring  拖曳环 offset=-(L_TAIL+W_RING-EMBED) 挤出 W_RING+EMBED——嵌入段体积重叠
三段主体纯串联（总长=L_NOSE+L_HULL+L_TAIL）；唯一允许段间重叠=翼贯穿+环嵌入。
"""
import cadquery as cq

PARAM_SCHEMA = {
    "ref_id": "towfish-recipe",
    "title": "侧扫声呐拖鱼参数化配方",
    "sonar_types": ["侧扫声呐", "side scan sonar", "拖鱼"],
    "required": [
        {"id": "body_radius",       "name_match": "主体半径",     "unit": "mm", "type": "number",
         "default": 100.0, "min": 40.0, "max": 300.0},
        {"id": "body_length",       "name_match": "主体耐压段长度", "unit": "mm", "type": "number",
         "default": 1200.0, "min": 400.0, "max": 2400.0},
        {"id": "nose_cone_length",  "name_match": "首锥长度",     "unit": "mm", "type": "number",
         "default": 150.0, "min": 60.0, "max": 400.0},
        {"id": "nose_tip_radius",   "name_match": "首端小端半径",  "unit": "mm", "type": "number",
         "default": 10.0, "min": 4.0, "max": 60.0},
        {"id": "tail_cone_length",  "name_match": "尾锥长度",     "unit": "mm", "type": "number",
         "default": 100.0, "min": 50.0, "max": 300.0},
        {"id": "tail_tip_radius",   "name_match": "尾端小端半径",  "unit": "mm", "type": "number",
         "default": 15.0, "min": 5.0, "max": 80.0},
        {"id": "fin_chord",         "name_match": "翼弦长",       "unit": "mm", "type": "number",
         "default": 400.0, "min": 200.0, "max": 900.0},
        {"id": "fin_half_span",     "name_match": "单侧翼展",     "unit": "mm", "type": "number",
         "default": 150.0, "min": 60.0, "max": 400.0},
        {"id": "fin_thickness",     "name_match": "翼厚",         "unit": "mm", "type": "number",
         "default": 10.0, "min": 4.0, "max": 30.0},
        {"id": "ring_outer_radius", "name_match": "拖曳环外半径",  "unit": "mm", "type": "number",
         "default": 40.0, "min": 20.0, "max": 100.0},
        {"id": "ring_inner_radius", "name_match": "拖曳环内半径",  "unit": "mm", "type": "number",
         "default": 25.0, "min": 12.0, "max": 80.0},
        {"id": "ring_width",        "name_match": "拖曳环宽度",    "unit": "mm", "type": "number",
         "default": 15.0, "min": 8.0, "max": 40.0},
        {"id": "ring_embed_length", "name_match": "拖曳环嵌入段长", "unit": "mm", "type": "number",
         "default": 20.0, "min": 10.0, "max": 50.0},
    ],
    "optional": [],
}

_PARAM_IDS = (
    "body_radius", "body_length", "nose_cone_length", "nose_tip_radius",
    "tail_cone_length", "tail_tip_radius", "fin_chord", "fin_half_span",
    "fin_thickness", "ring_outer_radius", "ring_inner_radius", "ring_width",
    "ring_embed_length",
)


def build(params: dict):
    """按 PARAM_SCHEMA 取参构造拖鱼（缺参 KeyError 原样抛——调用方必须先过 check_coverage）。

    五段构造逐字保留骨架 gold（见模块 docstring），尺寸全部来自 params。"""
    g = {k: float(params[k]) for k in _PARAM_IDS}
    R_HULL, L_HULL = g["body_radius"], g["body_length"]
    L_NOSE, R_NOSE_TIP = g["nose_cone_length"], g["nose_tip_radius"]
    L_TAIL, R_TAIL_TIP = g["tail_cone_length"], g["tail_tip_radius"]
    C_FIN, SPAN_FIN, T_FIN = g["fin_chord"], g["fin_half_span"], g["fin_thickness"]
    R_RING_OD = g["ring_outer_radius"]
    R_RING_ID = g["ring_inner_radius"]
    W_RING, EMBED = g["ring_width"], g["ring_embed_length"]

    nose = cq.Workplane('YZ').workplane(offset=L_HULL).circle(R_HULL) \
        .workplane(offset=L_NOSE).circle(R_NOSE_TIP).loft(combine=False)
    cyl = cq.Workplane('YZ').circle(R_HULL).extrude(L_HULL)
    tail = cq.Workplane('YZ').workplane(offset=-L_TAIL).circle(R_TAIL_TIP) \
        .workplane(offset=L_TAIL).circle(R_HULL).loft(combine=False)
    hull = nose.union(cyl).union(tail)
    fin = cq.Workplane('XY').box(C_FIN, SPAN_FIN * 2, T_FIN, centered=(True, True, False)) \
        .translate((L_HULL * 0.6, 0, -T_FIN / 2))
    ring = cq.Workplane('YZ').workplane(offset=-L_TAIL - W_RING + EMBED) \
        .circle(R_RING_OD).circle(R_RING_ID).extrude(W_RING + EMBED)
    result = hull.union(fin).union(ring)
    return result


# 抽样验证组（ADR-019 §4 判据 1：min/默认/max；expected_bbox 由参数确定性公式手算——
# x=L_NOSE+L_HULL+L_TAIL / y=max(2·SPAN_FIN, 2·R_HULL) / z=max(2·R_HULL, T_FIN)）
SAMPLES = [
    {
        "name": "default",
        "params": {
            "body_radius": 100.0, "body_length": 1200.0, "nose_cone_length": 150.0,
            "nose_tip_radius": 10.0, "tail_cone_length": 100.0, "tail_tip_radius": 15.0,
            "fin_chord": 400.0, "fin_half_span": 150.0, "fin_thickness": 10.0,
            "ring_outer_radius": 40.0, "ring_inner_radius": 25.0,
            "ring_width": 15.0, "ring_embed_length": 20.0,
        },
        "expected_bbox_mm": {"x": 1450.0, "y": 300.0, "z": 200.0},
        "expected_solids": 1,
    },
    {
        "name": "min",
        "params": {
            "body_radius": 60.0, "body_length": 800.0, "nose_cone_length": 100.0,
            "nose_tip_radius": 8.0, "tail_cone_length": 80.0, "tail_tip_radius": 10.0,
            "fin_chord": 300.0, "fin_half_span": 100.0, "fin_thickness": 8.0,
            "ring_outer_radius": 30.0, "ring_inner_radius": 20.0,
            "ring_width": 12.0, "ring_embed_length": 20.0,
        },
        "expected_bbox_mm": {"x": 980.0, "y": 200.0, "z": 120.0},
        "expected_solids": 1,
    },
    {
        "name": "max",
        "params": {
            "body_radius": 150.0, "body_length": 1600.0, "nose_cone_length": 200.0,
            "nose_tip_radius": 15.0, "tail_cone_length": 120.0, "tail_tip_radius": 20.0,
            "fin_chord": 500.0, "fin_half_span": 200.0, "fin_thickness": 12.0,
            "ring_outer_radius": 50.0, "ring_inner_radius": 35.0,
            "ring_width": 20.0, "ring_embed_length": 25.0,
        },
        "expected_bbox_mm": {"x": 1920.0, "y": 400.0, "z": 300.0},
        "expected_solids": 1,
    },
]
