# -*- coding: utf-8 -*-
"""涉密语义脱敏——占位与回填（T13.3，总体设计 §9.3 完整形态的机制层）。

§9.3 纵深防御：即便私有化部署，送大模型的文本仍前置脱敏——剥离项目代号、型号、
人员与单位名称等涉密项，以中性占位符 [SENS-n] 替换，大模型返回后按映射回填。
与 T11.6 键值脱敏（sanitize.py，密钥形态）分层：本模块管「语义身份类」，
由 SANITIZE_SEMANTIC 控制（默认开）。

机制完整、策略如实（A-14/A-16）：
- 提取为**规则级**：身份形态正则自动扫描（型号/代号/责任人/单位等赋值与代号形态）
  + 调用方显式注册项（精确串替换，如会话标题/项目名）——非 NER 模型，覆盖面以
  规则集为准，宁可漏不可滥（滥捕普通词会污染 prompt）；
- **数值型性能指标默认不脱敏**（SANITIZE_SEMANTIC_NUMERIC=0）：A2/A3/A4 工程链路的
  数值推导、CadQuery 建模、SymPy 校验依赖真实值在 LLM 侧参与算术，占位符无法参与
  计算，盲脱会破坏数值正确性——数值剥离需配合「占位化 + 确定性 solver 代入」的
  架构改造（生产化纵深改造项，如实留待）；机制保留：numeric_items 通道 +
  SANITIZE_SEMANTIC_NUMERIC 开关，改造后即可启用；
- 占位回填的固有边界：LLM 若改写（翻译/复述）敏感串而非回显占位符，restore 无法
  还原——§9.3 占位方案的已知限制，如实标注。

用法（per-call 映射，无全局状态——映射仅存活单次 chat 调用）：
    result = adapter.chat(messages, desensitize_items=["XX-07 项目", "张工"])
"""

from __future__ import annotations

import os
import re

_PLACEHOLDER = "[SENS_{n}]"  # 下划线形态：不匹配任何身份规则（避免占位符被二次捕获嵌套）


def _semantic_enabled() -> bool:
    return os.environ.get("SANITIZE_SEMANTIC", "1").strip().lower() not in ("0", "false", "no")


def _numeric_enabled() -> bool:
    return os.environ.get("SANITIZE_SEMANTIC_NUMERIC", "0").strip().lower() in ("1", "true", "yes")


# 身份形态（自动扫描）：域内常见「代号/型号/责任人/单位」赋值与代号形态。
# 规则级口径：型号/代号需带引导词或 ≥2 字母前缀的编号形态——单字母前缀（如 A-12 系
# 决策编号、F-35）刻意排除，避免把系统自身惯例编号误捕进占位符。
_IDENTITY_PATTERNS: list[re.Pattern[str]] = [
    # 型号/代号/番号 + 引导值（引号或裸串）
    re.compile(r"(?i)(?:型号|代号|番号)\s*[::：=]\s*[\"'「『]?([A-Za-z0-9_#\-·]{2,24})[\"'」』]?"),
    # 责任人/联系人（中文 2-4 字或英文标识）
    re.compile(r"(?:负责人|联系人|设计者|设计员|填报人)\s*[::：]\s*([\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z0-9_]{1,16})"),
    # 单位/部门（中文/字母数字组合）
    re.compile(r"(?:单位|部门|院所|厂所|所属单位)\s*[::：]\s*([\u4e00-\u9fa5A-Za-z0-9_（）()·\-]{2,24})"),
    # ≥2 字母前缀编号形态：SS-3C / XW-07 / GJB-1234（GJB 1234 空格形态不在内——军标引用需保留）
    re.compile(r"\b([A-Z]{2,6}-\d{1,4}[A-Za-z]?)\b"),
]


class Desensitizer:
    """per-call 语义脱敏器：mask 出站文本 + restore 响应文本，映射随实例存活。

    - identity 自动扫描（SANITIZE_SEMANTIC=1 默认开）；
    - extra_items：调用方显式注册的敏感串（精确替换；长串优先，避免短串截断）；
    - numeric_items：数值类显式注册项（默认关闭，SANITIZE_SEMANTIC_NUMERIC=1 启用）。
    """

    def __init__(
        self,
        extra_items: list[str] | None = None,
        numeric_items: list[str] | None = None,
        auto_identity: bool = True,
    ) -> None:
        self._mapping: dict[str, str] = {}
        self._auto = auto_identity and _semantic_enabled()
        items = list(extra_items or [])
        if _numeric_enabled():
            items += list(numeric_items or [])
        seen: set[str] = set()
        self._items: list[str] = []
        for it in items:
            if it and it not in seen:
                seen.add(it)
                self._items.append(it)

    def _register(self, value: str) -> str:
        """注册敏感原值 → 占位符（同原值复用同一占位符，保证回填一致性）。"""
        if value not in self._mapping:
            self._mapping[value] = _PLACEHOLDER.format(n=len(self._mapping) + 1)
        return self._mapping[value]

    def mask(self, text: str) -> str:
        if not text:
            return text
        # 1) 身份形态自动扫描（先行——占位符含字母数字，若显式替换先行会被编号规则二次捕获）。
        #    只替换捕获组、保留「型号：」等标签词（replace 于 group(0) 内定位 g1），restore 后原文完整。
        if self._auto:
            for pattern in _IDENTITY_PATTERNS:
                def _sub(m: re.Match[str]) -> str:
                    g1 = m.group(1)
                    return m.group(0).replace(g1, self._register(g1))
                text = pattern.sub(_sub, text)
        # 2) 显式注册项：精确串替换（长度降序，长串先替换）
        for item in sorted(self._items, key=len, reverse=True):
            text = text.replace(item, self._register(item))
        return text

    def mask_messages(self, messages: list[dict]) -> list[dict]:
        """整体 mask：字符串值逐段处理，其他类型原样保留（sanitize_messages 同构）。"""
        return [
            {k: (self.mask(v) if isinstance(v, str) else v) for k, v in m.items()}
            for m in messages
        ]

    def restore(self, text: str) -> str:
        """按映射回填：占位符 → 原值。LLM 未回显的占位符保持替换语义（无害 no-op）。"""
        if not text or not self._mapping:
            return text
        for original, placeholder in self._mapping.items():
            text = text.replace(placeholder, original)
        return text

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._mapping)


def make_desensitizer(
    desensitize_items: list[str] | None = None,
    numeric_items: list[str] | None = None,
) -> Desensitizer | None:
    """chat() 挂点工厂：语义脱敏关闭且无显式注册项时返回 None（零开销直通）；
    关闭但显式注册了项时仍生效（显式注册视为调用方的确定性脱敏意图）。"""
    if not _semantic_enabled() and not (desensitize_items or numeric_items):
        return None
    return Desensitizer(extra_items=desensitize_items, numeric_items=numeric_items)
