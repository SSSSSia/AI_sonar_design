"""清洗分块（T3.2，宪法 §6 ③）。

策略：**章节优先**——解析单元（页/章节/段落）不跨边界；单元超长才内部切分，
切分点优先落在换行/句读，重叠可调（KB_CHUNK_OVERLAP）。每个 chunk 携带 doc_id + location。
"""

from __future__ import annotations

from kb.parsers import ParsedUnit

_BREAK_CHARS = "。；！？\n"


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    """超长文本内部切分：优先在断句字符处断开，带 overlap。"""
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    start = 0
    step = max(size - overlap, 1)
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # 在窗口尾部 1/3 内找最后一个断句点
            window = text[start:end]
            best = max(window.rfind(c) for c in _BREAK_CHARS)
            cut = start + best + 1
            if cut > start + step // 2:
                end = cut
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


def chunk_units(
    units: list[ParsedUnit],
    size: int,
    overlap: int,
) -> list[dict[str, str]]:
    """解析单元 → chunks。返回 [{location, text}]（chunk_index 由调用方/存储层编号）。"""
    if overlap >= size:
        raise ValueError(f"overlap({overlap}) 必须小于 size({size})")
    chunks: list[dict[str, str]] = []
    for unit in units:
        text = unit.text.strip()
        if not text:
            continue
        if len(text) <= size:
            chunks.append({"location": unit.location, "text": text})
            continue
        pieces = _split_long(text, size, overlap)
        for k, piece in enumerate(pieces, 1):
            suffix = "" if len(pieces) == 1 else f"#块{k}"
            chunks.append({"location": f"{unit.location}{suffix}", "text": piece})
    return chunks
