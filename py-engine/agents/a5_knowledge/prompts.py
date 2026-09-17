"""A5 知识沉淀提示词（T2.3）。LLM 仅做候选整理（A-07 白名单：沉淀候选整理），
数值与事实全部来自 IR2/IR4 结构化输入，LLM 不得虚构参数值。"""

CANDIDATE_EXTRACTION_PROMPT = """\
你是声呐装备设计知识整理专家。以下是一次设计会话的结构化产物（参数表 + 校验结论）。

请从中整理出值得沉淀到知识图谱的经验条目。**只整理给定输入中明确出现的知识与数值，
禁止虚构**。输出 JSON，格式如下（candidates 数组 0~8 条，可为空）：

```json
{"candidates": [
  {"type": "constraint",
   "payload": {"name": "约束/经验条目名", "expression": "数学表达式或规则描述",
                "context": "一句话适用条件说明"}},
  {"type": "case",
   "payload": {"name": "案例名（装备类型+规模）",
                "params": [{"name": "参数名", "value": <数值>, "unit": "单位"}],
                "context": "案例来源/适用性一句话"}},
  {"type": "relation",
   "payload": {"from": {"type": "DesignParameter|Component|DesignConstraint|...", "name": "实体名"},
                "to": {"type": "...", "name": "实体名"},
                "relation": "RELATION_VERB（英文大写下划线）",
                "context": "关系说明一句话"}}
]}
```

整理原则：
1. 优先沉淀"可复用的一般性知识"：公式结论、设计约束、典型案例参数取值、实体间关系；
2. 单次会话特有的临时信息（会话 id、批次号等）不沉淀；
3. params 只允许从给定参数表中挑选条目，value/unit 原样照抄；
4. 拿不准是否值得沉淀时，宁可不要。
"""
