"""A2 方案生成（C2）★ 系统枢纽。

边界（宪法 §3.1 / P2 §2.3）：
- 做什么：检索结果 → 设计文档（md）+ 带出处参数表，输出 IR2——IR2 是全系统枢纽，
  向上承接需求、向下驱动 CAD、是合规校验与溯源的依据；
- 不做什么：不解复杂隐式方程、不写 CAD 代码。

参数来源四分类强制（A-06）：每个参数必须带
source_type ∈ {formula, case, standard, estimated} + source_ref + derivation + trace_id，
缺来源的参数不允许进入 IR2。
公式类参数取值必须由代码计算（SymPy 等），不得由 LLM 给出数值（A-07）。
输入 IR1 → 输出 IR2；契约见 contracts/ir/ir2.schema.json。
"""
