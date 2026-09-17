"""A1 需求解析（C1）。

边界（宪法 §3.1 / P2 §2.3）：
- 做什么：拆解自然语言需求、规划 4~8 条子查询、并行检索，输出 IR1
  （结构化目标 + missing_items + sub_queries + retrieved_context）；
- 不做什么：不推导几何参数、不赋值。

LLM 白名单用途（A-07）：需求理解与子查询规划。
输入 IR0 → 输出 IR1；契约见根目录 contracts/ir/ir1.schema.json（A-04 单一事实源）。
"""
