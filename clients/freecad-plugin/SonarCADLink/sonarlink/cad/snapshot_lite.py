"""轻量文档快照（T8.4）——单槽位"执行前快照 + 手动恢复"。

与 CadAgent 的 SnapshotManager（撤销栈，关闭即删）不同：这里的定位只是安全网——
每次 execute_edit 前用 doc.saveCopy() 落一份临时 FCStd（不改变文档 FileName），
面板提供"恢复到最近快照"按钮；平台侧版本链才是真正的持久化与回退机制。
"""

from __future__ import annotations

import os
import tempfile

_last_snapshot: dict = {"path": None, "doc_name": None}


def take(doc) -> str | None:
    """快照当前文档（saveCopy 不改 FileName）。失败返回 None（尽力而为，不阻断执行）。"""
    try:
        if doc is None:
            return None
        path = os.path.join(tempfile.gettempdir(), f"sonarlink_snapshot_{doc.Name}.FCStd")
        doc.saveCopy(path)
        _last_snapshot["path"] = path
        _last_snapshot["doc_name"] = doc.Name
        return path
    except Exception:
        return None


def last_snapshot_path() -> str | None:
    return _last_snapshot["path"]


def restore() -> tuple[bool, str]:
    """关闭当前文档并从最近快照重开。返回 (成功?, 消息)。"""
    import FreeCAD

    path = _last_snapshot["path"]
    if not path or not os.path.exists(path):
        return False, "没有可恢复的快照"
    name = _last_snapshot["doc_name"]
    try:
        if name and name in FreeCAD.listDocuments():
            FreeCAD.closeDocument(name)
        FreeCAD.openDocument(path)
        return True, f"已恢复快照 {os.path.basename(path)}"
    except Exception as exc:
        return False, f"恢复失败：{type(exc).__name__}: {exc}"
