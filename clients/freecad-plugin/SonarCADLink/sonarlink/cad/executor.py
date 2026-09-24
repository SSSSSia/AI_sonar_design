"""工具执行器（T8.4）——服务端 AgentLoop 下发的 execute_edit_code 在 FreeCAD 内落地。

流程（改编自 CadAgent agent/tools.py 的 execute_code，收敛为编辑契约）：
  strip_markdown → compile 语法预检 → 定位/确保文档 → 注入 model（当前主实体）→
  执行前快照（安全网）→ 受限 builtins exec → 质量门禁（quality.py）→ 结构化结果。

返回契约（与服务端 cad_agent/loop.py 的 tool_executor 对齐）：
  {ok: bool, stdout: str|None, quality: {status: pass|warn|fail, issues: [code: message]},
   doc_summary: str|None, error: str|None}

诚实边界：FreeCAD 主线程内执行无法强制超时（架构固有面，与业界 MCP 先例一致）——
缓解 = 语法预检 + 面板 Stop 中断标志 + 服务端工具等待超时（PHASE-008 §2.4）。
"""

from __future__ import annotations

import contextlib
import io
import math
import traceback

import FreeCAD
import Part

from sonarlink.cad.cad_helpers import (
    cq_show,
    ensure_doc,
    extract_solid,
    make_arc_handle,
    make_box_handle,
    make_hollow_cylinder,
    make_ring,
    safe_cut,
    safe_fuse,
)
from sonarlink.cad.code_fixes import error_hint, pre_validate_code
from sonarlink.cad.cq import Workplane as _CQWorkplane, _cq_module
from sonarlink.cad.doc_analyzer import analyze_document
from sonarlink.cad.quality import analyze_document_quality
from sonarlink.text_utils import strip_markdown

SAFE_BUILTINS = {
    "__import__": __import__,
    "print": print, "range": range, "len": len, "int": int,
    "float": float, "str": str, "list": list, "dict": dict,
    "tuple": tuple, "set": set, "bool": bool, "bytes": bytes,
    "abs": abs, "min": min, "max": max, "round": round, "sum": sum,
    "sorted": sorted, "reversed": reversed, "enumerate": enumerate,
    "zip": zip, "map": map, "filter": filter, "any": any, "all": all,
    "isinstance": isinstance, "type": type, "hasattr": hasattr,
    "getattr": getattr, "setattr": setattr, "repr": repr,
    "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError,
    "RuntimeError": RuntimeError, "Exception": Exception,
    "NotImplementedError": NotImplementedError,
    "AttributeError": AttributeError, "OSError": OSError,
}

# 跨工具调用存活的用户变量（含形状对象；换会话/恢复快照后由 _clean_stale 清理）
_EXEC_NAMESPACE: dict = {}
_BUILTIN_NAMES = frozenset({
    "FreeCAD", "FreeCADGui", "Part", "math", "doc", "model", "__builtins__",
    "extract_solid", "safe_fuse", "safe_cut", "make_hollow_cylinder", "make_ring",
    "make_box_handle", "make_arc_handle", "ensure_doc", "cq", "Workplane", "cq_show",
    "result",
})

_stop_flag = {"stopped": False}


def request_stop() -> None:
    """面板 Stop 按钮：工具间检查，尽力中断后续执行。"""
    _stop_flag["stopped"] = True


def _is_stopped() -> bool:
    return _stop_flag["stopped"]


# ----------------------------------------------------------------------
def import_step(step_path: str) -> str:
    """导入平台版本 STEP 到新文档，返回文档名。模型即从这里来（丢参数历史——局限如实）。"""
    import FreeCAD

    doc_name = "SonarModel"
    if doc_name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(doc_name)
    doc = FreeCAD.newDocument(doc_name)
    import Import  # FreeCAD 模块：STEP/IGES 导入导出

    Import.insert(step_path, doc.Name)
    doc.recompute()
    if not main_model_shape(doc):
        raise RuntimeError("STEP 导入后文档中没有实体——请确认版本产物有效")
    return doc.Name


def _as_shape(x):
    """Workplane / 文档对象 / Shape 统一转 Shape。"""
    if hasattr(x, "solid") and callable(x.solid):
        return x.solid()
    if hasattr(x, "Shape") and not callable(x.Shape):
        return x.Shape
    return x


def main_model_shape(doc):
    """当前文档的主实体（体积最大的 Solid 的宿主对象 Shape）——即 `model` 变量。"""
    if doc is None:
        return None
    best, best_vol = None, -1.0
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            solids = shape.Solids or ([shape] if shape.ShapeType == "Solid" else [])
        except Exception:
            continue
        for s in solids:
            try:
                vol = s.Volume
            except Exception:
                continue
            if vol > best_vol:
                best, best_vol = s, vol
    return best


def _clean_stale() -> None:
    """恢复快照/换版本后清掉失效的 C++ 形状引用（CadAgent 同款防护）。"""
    import FreeCAD

    active = FreeCAD.ActiveDocument
    for key, val in list(_EXEC_NAMESPACE.items()):
        if isinstance(val, (int, float, str, bool, type(None))):
            continue
        try:
            doc_attr = getattr(val, "Document", None)
            if doc_attr is not None and doc_attr is not active:
                del _EXEC_NAMESPACE[key]
        except (ReferenceError, RuntimeError):
            _EXEC_NAMESPACE.pop(key, None)


# ----------------------------------------------------------------------
def execute_edit(code: str, label: str = "Edit") -> dict:
    """执行一条编辑脚本，返回协议结果 dict（永不抛异常——错误进 error 字段）。"""
    result = _execute_edit_inner(code, label)
    if _is_stopped():
        result["error"] = (result.get("error") or "") + "（用户已请求停止）"
    return result


def _err(error: str, stdout: str | None = None) -> dict:
    return {"ok": False, "stdout": stdout, "quality": None, "doc_summary": None, "error": error}


def _execute_edit_inner(code: str, label: str) -> dict:
    code = strip_markdown(code or "").strip()
    if not code:
        return _err("代码为空")
    if _is_stopped():
        return _err("用户已请求停止")

    ok, syntax_err = pre_validate_code(code)
    if not ok:
        return _err(f"语法错误，未执行：\n{syntax_err}")

    doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = ensure_doc("SonarModel")
    _clean_stale()
    model = main_model_shape(doc)
    if model is None:
        return _err("当前文档没有实体模型——请先点『拉取模型』导入平台版本（再执行编辑）")

    from snapshot_lite import take

    take(doc)  # 安全网快照（尽力而为）

    namespace = {
        "FreeCAD": FreeCAD, "Part": Part, "math": math,
        "doc": doc, "model": model, "__builtins__": SAFE_BUILTINS,
        "extract_solid": extract_solid, "safe_fuse": safe_fuse, "safe_cut": safe_cut,
        "make_hollow_cylinder": make_hollow_cylinder, "make_ring": make_ring,
        "make_box_handle": make_box_handle, "make_arc_handle": make_arc_handle,
        "ensure_doc": ensure_doc,
        "cq": _cq_module, "Workplane": _CQWorkplane, "cq_show": cq_show,
    }
    namespace.update(_EXEC_NAMESPACE)

    stdout_capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_capture):
            exec(code, namespace)  # noqa: S102 - 仅执行本平台下发的代码（架构固有面）
    except Exception as exc:
        _save_user_vars(namespace)
        tb = traceback.format_exc()
        hint, _fixed = error_hint(exc, code)
        msg = f"{type(exc).__name__}: {exc}"
        if hint:
            msg += f"\n修复提示：{hint}"
        return {"ok": False, "stdout": stdout_capture.getvalue() or None,
                "quality": None, "doc_summary": _doc_state(doc), "error": f"{msg}\n{tb}"}

    _save_user_vars(namespace)

    # result 契约校验（编辑必须产出新形体）
    result = namespace.get("result")
    if result is None:
        return _err("脚本未赋值 result——编辑契约要求最后一行 result = <单一实体 Shape>",
                    stdout_capture.getvalue() or None)
    try:
        _as_shape(result)
    except Exception as exc:
        return _err(f"result 不是有效形体：{exc}", stdout_capture.getvalue() or None)

    # 质量门禁（quality.py 结构化报告 → 协议 quality dict；severity ok→pass 归一）
    report = analyze_document_quality(doc)
    quality = {
        "status": {"ok": "pass"}.get(report.severity, report.severity),
        "issues": [f"{i.code}: {i.message}" + (f"（{i.suggestion}）" if i.suggestion else "")
                   for i in report.issues],
    }
    return {"ok": True, "stdout": stdout_capture.getvalue() or None, "quality": quality,
            "doc_summary": _doc_state(doc), "error": None}


def _doc_state(doc) -> str | None:
    try:
        state = analyze_document(doc, concise=True)
        return None if "(No active document)" in state else state
    except Exception:
        return None


def _save_user_vars(namespace: dict) -> None:
    for name, value in namespace.items():
        if name.startswith("_") or name in _BUILTIN_NAMES:
            continue
        _EXEC_NAMESPACE[name] = value


def reset_session_state() -> None:
    _EXEC_NAMESPACE.clear()
    _stop_flag["stopped"] = False


def selection_context() -> dict | None:
    """把设计人员当前在 3D 视图的点选转成上下文 dict（无选择返回 None）。"""
    try:
        import FreeCADGui

        sel = FreeCADGui.Selection.getSelectionEx()
        if not sel:
            return None
        shapes = []
        for s in sel:
            for sub in s.SubElementNames or [""]:
                shapes.append((s.ObjectName, sub))
        first = sel[0]
        shape = first.Object.Shape
        bbox = shape.BoundBox
        return {
            "count": len(shapes),
            "elements": [f"{obj}.{sub}" for obj, sub in shapes[:5]],
            "bbox_mm": [round(bbox.XLength, 2), round(bbox.YLength, 2), round(bbox.ZLength, 2)],
            "center_mm": [round(bbox.Center.x, 2), round(bbox.Center.y, 2), round(bbox.Center.z, 2)],
            "kind": first.Object.TypeId,
        }
    except Exception:
        return None
