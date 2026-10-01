# -*- coding: utf-8 -*-
"""T11.5 单测：军标格式 Word 导出（docgen/export_docx.py）。导出字节流可被 python-docx
重开，章节标题与表格行数与快照数据一致（A-16：内置模板口径如实断言）。"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docgen.export_docx import export_session_docx

IR2 = {
    "design_document": "# 总体方案\n\n## 设计说明\n\n拖曳阵分段设计，正文一段。\n\n| 参数 | 取值 |\n|---|---|\n| 缆长 | 100 |\n",
    "parameters": [
        {"id": "P-001", "name": "缆长", "value": 100, "unit": "m", "source_type": "formula",
         "source_ref": "REG-LEN#L=N*d", "derivation": "len=100 mm", "confidence": "high",
         "trace_id": "t-1"},
        {"id": "P-002", "name": "外径", "value": 27, "unit": "mm", "source_type": "standard",
         "source_ref": "GJB 23B", "derivation": "", "confidence": "medium", "trace_id": "t-2"},
        {"id": "P-003", "name": "间距", "value": 1.5, "unit": "m", "source_type": "estimated",
         "source_ref": "", "derivation": "", "confidence": "medium", "trace_id": "t-3"},
    ],
}
IR4 = {
    "overall": "pass",
    "csr": 87.5,
    "issues": [
        {"constraint_id": "CHK-GEO-NUM", "severity": "warning", "message": "间距偏差 1.5%", "suggestion": None},
        {"constraint_id": "CHK-STD", "severity": "error", "message": "外径超限", "suggestion": "back_to_A2"},
    ],
}


def _export() -> bytes:
    return export_session_docx(
        {"id": "s-1", "title": "拖曳阵基段设计", "pipeline_status": "paused"},
        IR2, ir3=None, ir4=IR4, ir2_version=3,
    )


def _reopen(data: bytes):
    import docx

    return docx.Document(io.BytesIO(data))


def test_export_reopenable_and_headings():
    doc = _reopen(_export())
    texts = [p.text for p in doc.paragraphs]
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith(("Heading", "Title"))]
    assert "拖曳阵基段设计" in headings  # 封面 Title
    for chapter in ("设计文档", "参数表", "溯源依据", "校验结论"):
        assert chapter in headings, f"缺少章: {chapter}"
    # Markdown 正文渲染：#/## 变标题
    assert "总体方案" in headings and "设计说明" in headings
    assert "拖曳阵分段设计，正文一段。" in texts


def test_export_param_table_rows():
    doc = _reopen(_export())
    # 封面 meta 表 + MD 表 + 参数表(1+3行) + issue 表 —— 找 7 列那张
    tables = [t for t in doc.tables if len(t.columns) == 7]
    assert len(tables) == 1, f"参数表应恰 1 张 7 列，实际 {len(tables)}"
    t = tables[0]
    assert len(t.rows) == 4  # 表头 + 3 参数
    assert [c.text for c in t.rows[0].cells] == ["参数ID", "名称", "数值", "单位", "来源分类", "来源依据", "置信度"]
    assert t.cell(1, 0).text == "P-001" and t.cell(1, 4).text == "公式计算"


def test_export_validation_chapter_content():
    doc = _reopen(_export())
    texts = [p.text for p in doc.paragraphs]
    assert any("通过" in t and "总体结论" in t for t in texts)
    assert any("87.5 / 100" in t for t in texts)  # CSR
    issue_tables = [t for t in doc.tables if len(t.columns) == 4 and t.rows[0].cells[0].text == "约束ID"]
    assert len(issue_tables) == 1 and len(issue_tables[0].rows) == 3
    assert issue_tables[0].cell(2, 1).text == "错误"  # severity error 中文化


def test_export_missing_materials_honest():
    """素材缺失各章如实标注，不造假（IR4 None → 「尚未生成」）。"""
    data = export_session_docx({"id": "s-2", "title": "x"}, ir2=None, ir3=None, ir4=None, ir2_version=1)
    doc = _reopen(data)
    texts = [p.text for p in doc.paragraphs]
    assert any("IR2 尚未生成" in t for t in texts)
    assert any("IR4 校验报告尚未生成" in t for t in texts)
