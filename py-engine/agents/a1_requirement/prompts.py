"""A1 需求解析提示词模板（T1.3，迁移自旧仓库 knowledge_stage 规划维度表 ★拆用）。

来源：Sonar_cad_design/agent/stages/knowledge_stage.py _plan_queries 的维度清单；
v2 扩展为"需求理解 + 查询规划"一体化输出（objectives + missing_items + sub_queries）。
"""

# 必须覆盖的检索信息维度（不可遗漏；对齐旧版规划 prompt，按声呐 CAD 领域）
RETRIEVAL_DIMENSIONS = [
    "系统总体组成与结构布局",
    "各部件/组件的精确尺寸参数（外径、内径、长度、壁厚）",
    "内部元器件规格（数量、间距、尺寸）",
    "装配关系与顺序",
    "材料与工艺约束",
    "特殊设计要求（弯曲轨迹、密封等）",
    "船上/干端/母船端设备规格与空间布局（如适用，必须单独查询）",
    "各子系统之间的连接关系与接口（如适用）",
]

PLAN_PROMPT = """你是一个水下声呐装备设计需求解析专家。分析用户需求，输出 JSON（不要任何其他文字）：

{{
  "objectives": [
    {{"dimension": "目标维度（如 声阵规模/工作深度/频段/阵元间距）", "value": 需求中显式给出的值或 null, "explicit": true/false}}
  ],
  "missing_items": ["需求未明确、需要后续确认或从知识库补充的关键设计信息"],
  "sub_queries": ["检索查询1", "检索查询2", ...]
}}

要求：
1. objectives 只登记用户需求中提到或可直接推断的目标，不推导几何参数、不赋值（A-01 边界）；
2. missing_items 如实列出缺失的关键信息，禁止编造（A-16）；
3. sub_queries 规划 4~8 条精确检索查询，必须覆盖以下全部维度：
{dimensions}
4. 对由多个子系统组成的复杂设备（水下部分+船上部分），每个子系统必须有专门查询；
5. 仅输出 JSON 对象。
"""

FALLBACK_QUERIES_TEMPLATE = [
    "{req}的系统总体组成与结构布局",
    "{req}各部件的精确尺寸参数",
    "{req}内部元器件参数与装配关系",
    "{req}的材料规格与设计约束",
]

SYSTEM_PROMPT = "你是水下声呐装备设计需求解析专家。仅输出 JSON 对象。"
