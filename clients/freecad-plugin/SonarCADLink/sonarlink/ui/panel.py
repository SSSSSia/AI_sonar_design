"""SonarCADLink 聊天面板（T8.4）——后端大脑模式的对话界面。

与 CadAgent ui/panel.py 的差异（如实记录）：
- 无本地 LLM 线程/会话存储：对话状态全部在平台（panel 只渲染 + 轮询）；
- BackendWorker(QThread) 常驻长轮询工具调用 → 信号回主线程执行 → 回传结果；
- 轮次收敛后自动导出 STEP/FCStd 上传定稿（保存机制=平台版本链，本地不再负责持久化）。

线程纪律：FreeCAD API 只在主线程碰（Qt 槽内）；HTTP 在 worker 线程；
轮次状态用 QTimer 轮询（2s），不引入 WS 依赖。
"""

from __future__ import annotations

import tempfile
import traceback

import FreeCAD
import FreeCADGui
from PySide6 import QtCore, QtWidgets  # 裸名 import QtCore 在 FreeCAD 1.1 不可用（真机实测）

from sonarlink.cad import snapshot_lite
from sonarlink.cad import executor
from sonarlink.client.platform_client import Client, PlatformError
from sonarlink.ui import chat_renderer, theme


class BackendWorker(QtCore.QThread):
    """常驻后台线程：工具调用长轮询 + 结果回传（HTTP 专用，不碰 FreeCAD）。"""

    toolCallReceived = QtCore.Signal(dict)
    connectionStateChanged = QtCore.Signal(str)

    def __init__(self, client: Client, parent=None):
        super().__init__(parent)
        self._client = client
        self._tune_id: str | None = None
        self._cursor = 0
        self._stop = False
        self._pending: dict[int, tuple] = {}  # call_id -> (result_dict, threading.Event)
        import threading

        self._lock = threading.Lock()

    def set_tune(self, tune_id: str | None, cursor: int = 0) -> None:
        self._tune_id = tune_id
        self._cursor = cursor

    def stop(self) -> None:
        self._stop = True

    def submit_result(self, call_id: int, result: dict) -> None:
        with self._lock:
            pending = self._pending.pop(call_id, None)
        if pending:
            result_dict, event = pending
            result_dict.update(result)
            event.set()

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: C901
        while not self._stop:
            tune_id = self._tune_id
            if not tune_id or not self._client.token:
                self.msleep(800)
                continue
            try:
                resp = self._client.poll_tool_calls(tune_id, self._cursor, wait=15)
            except PlatformError as exc:
                self.connectionStateChanged.emit(f"长轮询异常：{exc.detail}")
                self.msleep(2000)
                continue
            except Exception:
                self.msleep(2000)
                continue
            for call in resp.get("calls", []):
                self._cursor = max(self._cursor, call["call_id"])
                import threading

                result: dict = {}
                event = threading.Event()
                with self._lock:
                    self._pending[call["call_id"]] = (result, event)
                self.toolCallReceived.emit(call)
                if not event.wait(timeout=240):  # 主线程卡死/未响应的兜底（服务端另有 180s 超时）
                    with self._lock:
                        self._pending.pop(call["call_id"], None)
                    result = {"ok": False, "error": "插件执行等待超时（主线程未响应）"}
                try:
                    self._client.post_tool_result(tune_id, call["call_id"], result)
                except PlatformError as exc:
                    self.connectionStateChanged.emit(f"回传失败：{exc.detail}")


class AgentPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("SonarCAD Link · 平台微调", parent)
        self.setObjectName("SonarCADLinkPanel")
        self._client = Client(self._saved_url())
        self._worker = BackendWorker(self._client)
        self._tune_id: str | None = None
        self._round_no: int | None = None
        self._rendered_tools = 0
        self._round_timer = QtCore.QTimer(self)
        self._round_timer.setInterval(2000)
        self._round_timer.timeout.connect(self._poll_round)
        self._colors = {}
        self._build_ui()
        self._worker.toolCallReceived.connect(self._on_tool_call)
        self._worker.connectionStateChanged.connect(self._on_conn_note)
        self._worker.start()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _saved_url(self) -> str:
        settings = QtCore.QSettings("SonarCAD", "SonarCADLink")
        return settings.value("platform_url", "http://127.0.0.1:8080")

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        # ---- 登录区 ----
        login_row = QtWidgets.QHBoxLayout()
        self.url_edit = QtWidgets.QLineEdit(self._saved_url())
        self.url_edit.setPlaceholderText("平台地址")
        self.user_edit = QtWidgets.QLineEdit()
        self.user_edit.setPlaceholderText("平台账号")
        self.user_edit.setText(QtCore.QSettings("SonarCAD", "SonarCADLink").value("username", ""))
        self.pwd_edit = QtWidgets.QLineEdit()
        self.pwd_edit.setPlaceholderText("密码（不落盘）")
        self.pwd_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.login_btn = QtWidgets.QPushButton("登录")
        self.login_btn.clicked.connect(self._on_login)
        self.login_status = QtWidgets.QLabel("未登录")
        for w in (self.url_edit, self.user_edit, self.pwd_edit):
            w.setMaximumWidth(170)
        login_row.addWidget(self.url_edit)
        login_row.addWidget(self.user_edit)
        login_row.addWidget(self.pwd_edit)
        login_row.addWidget(self.login_btn)
        login_row.addWidget(self.login_status)
        login_row.addStretch(1)
        layout.addLayout(login_row)

        # ---- 会话区（登录后自动列出可微调会话，选中即开；手输 ID 仅备用） ----
        sess_row = QtWidgets.QHBoxLayout()
        self.sess_combo = QtWidgets.QComboBox()
        self.sess_reload_btn = QtWidgets.QPushButton("刷新")
        self.sess_reload_btn.clicked.connect(self._refresh_sessions)
        self.manual_btn = QtWidgets.QPushButton("手输 ID")
        self.manual_btn.setCheckable(True)
        self.open_btn = QtWidgets.QPushButton("打开/新建微调")
        self.open_btn.clicked.connect(self._on_open_tune)
        self.pull_btn = QtWidgets.QPushButton("拉取模型")
        self.pull_btn.clicked.connect(self._on_pull_model)
        self.sess_edit = QtWidgets.QLineEdit()
        self.sess_edit.setPlaceholderText("手动粘贴设计会话 ID（备用）")
        self.sess_edit.setVisible(False)
        self.sess_edit.setMaximumWidth(240)
        self.manual_btn.toggled.connect(self.sess_edit.setVisible)
        self.tune_status = QtWidgets.QLabel("")
        sess_row.addWidget(self.sess_combo, 1)
        sess_row.addWidget(self.sess_reload_btn)
        sess_row.addWidget(self.manual_btn)
        sess_row.addWidget(self.open_btn)
        sess_row.addWidget(self.pull_btn)
        sess_row.addWidget(self.tune_status)
        layout.addLayout(sess_row)

        # ---- 对话区 ----
        self.chat = QtWidgets.QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        layout.addWidget(self.chat, 1)

        # ---- 输入区 ----
        self.input = QtWidgets.QTextEdit()
        self.input.setMaximumHeight(72)
        self.input.setPlaceholderText("描述要怎样修改模型（Enter 发送 / Ctrl+Enter 换行）")
        layout.addWidget(self.input)
        btn_row = QtWidgets.QHBoxLayout()
        self.send_btn = QtWidgets.QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        self.stop_btn = QtWidgets.QPushButton("停止")
        self.stop_btn.clicked.connect(self._on_stop)
        self.restore_btn = QtWidgets.QPushButton("恢复最近快照")
        self.restore_btn.clicked.connect(self._on_restore)
        self.finalize_btn = QtWidgets.QPushButton("定稿")
        self.finalize_btn.clicked.connect(self._on_finalize)
        self.close_tune_btn = QtWidgets.QPushButton("结束微调")
        self.close_tune_btn.clicked.connect(self._on_close_tune)
        btn_row.addWidget(self.send_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.restore_btn)
        btn_row.addWidget(self.finalize_btn)
        btn_row.addWidget(self.close_tune_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.sess_combo, self.sess_reload_btn, self.sess_edit, self.open_btn,
                  self.pull_btn, self.send_btn, self.finalize_btn, self.close_tune_btn):
            w.setEnabled(on)

    def _refresh_theme(self) -> None:
        self._colors = theme.get_theme_colors(self.palette()).__dict__
        self.chat.setStyleSheet(f"background:{self._colors.get('bg_chat', '#ffffff')};")

    def showEvent(self, event) -> None:  # noqa: N802
        self._refresh_theme()
        super().showEvent(event)

    # ------------------------------------------------------------------
    # 聊天渲染
    # ------------------------------------------------------------------
    def _append_bubble(self, html: str) -> None:
        self.chat.append(html)

    def _append_md(self, text: str, who: str) -> None:
        bg = "#e8f0fe" if who == "user" else "#f5f5f5"
        title = "我" if who == "user" else "Agent"
        self._append_bubble(
            f"<table width='100%'><tr><td style='background:{bg};border-radius:6px;padding:6px'>"
            f"<b>{chat_renderer.esc(title)}</b><br>{chat_renderer.markdown_to_html(text, self._colors)}"
            f"</td></tr></table>")

    def _append_note(self, text: str) -> None:
        self._append_bubble(f"<span style='color:#888;font-size:small'>{chat_renderer.esc(text)}</span><br>")

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    def _on_login(self) -> None:
        url = self.url_edit.text().strip()
        user = self.user_edit.text().strip()
        pwd = self.pwd_edit.text()
        if not (url and user and pwd):
            self.login_status.setText("地址/账号/密码不能为空")
            return
        self._client.base_url = url.rstrip("/")
        settings = QtCore.QSettings("SonarCAD", "SonarCADLink")
        settings.setValue("platform_url", url)
        settings.setValue("username", user)
        self.login_btn.setEnabled(False)
        self.login_status.setText("登录中…")
        try:
            self._client.login(user, pwd)
            self.login_status.setText("已登录")
            self._set_enabled(True)
            self._refresh_sessions()
        except PlatformError as exc:
            self.login_status.setText(f"登录失败：{exc.detail}")
        finally:
            self.login_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # 微调会话
    # ------------------------------------------------------------------
    @staticmethod
    def _tunable(s: dict) -> bool:
        """可微调 = 已到审阅关②（A3 出图完成、流程暂停），或已有进行中的微调会话。"""
        return (s.get("tuneStatus") == "active"
                or (s.get("pipelineStatus") == "paused" and s.get("currentStage") == "gate2"))

    def _refresh_sessions(self) -> None:
        try:
            rows = [s for s in self._client.list_sessions() if self._tunable(s)]
        except PlatformError as exc:
            self.tune_status.setText(f"会话列表获取失败：{exc.detail}")
            return
        self.sess_combo.clear()
        for s in rows:
            label = s.get("title") or "未命名会话"
            if s.get("projectName"):
                label = f"{s['projectName']} / {label}"
            state = "微调中" if s.get("tuneStatus") == "active" else "待审阅关② · 可微调"
            self.sess_combo.addItem(f"{label}（{state} · {str(s.get('id') or '')[:8]}）", s.get("id"))
        if not rows:
            self.sess_combo.addItem("暂无可微调会话——先在平台把会话推进到审阅关②", None)
        self.tune_status.setText(f"可微调会话 {len(rows)} 个")

    def _on_open_tune(self) -> None:
        sid = self.sess_combo.currentData() or self.sess_edit.text().strip()
        if not sid:
            QtWidgets.QMessageBox.information(
                self, "SonarCAD Link",
                "没有选中的可微调会话。请先在平台把设计会话推进到审阅关②（A3 出图完成），或点「手输 ID」粘贴会话 ID。")
            return
        try:
            sessions = self._client.list_tunes(sid).get("sessions", [])
            active = next((s for s in sessions if s.get("status") == "active"), None)
            if active:
                self._tune_id = active["id"]
                self._append_note(f"已接入进行中的微调会话 {self._tune_id}（{len(active.get('rounds', []))} 轮）")
            else:
                tune = self._client.create_tune(sid)
                self._tune_id = tune["id"]
                self._append_note(f"已创建微调会话（基线 IR3 v{tune.get('baseline_ir_version')}）")
        except PlatformError as exc:
            QtWidgets.QMessageBox.warning(self, "打开微调失败", exc.detail)
            return
        self._worker.set_tune(self._tune_id)
        self.tune_status.setText("微调进行中")

    def _on_pull_model(self) -> None:
        if not self._tune_id:
            return
        try:
            ctx = self._client.get_context(self._tune_id)
        except PlatformError as exc:
            QtWidgets.QMessageBox.warning(self, "拉取失败", exc.detail)
            return
        import os

        dest = os.path.join(tempfile.gettempdir(), ctx.get("filename") or "model.step")
        self._client.download_to(ctx["step_url"], dest)
        doc_name = executor.import_step(dest)
        executor.reset_session_state()
        self._append_note(f"已导入平台 IR3 v{ctx.get('version')} 模型（文档 {doc_name}）。"
                          "局限：STEP 导入不含参数化历史，编辑以形体布尔为主。")

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def _on_send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or not self._tune_id:
            return
        self.input.clear()
        selection = executor.selection_context()
        sel_note = ""
        if selection:
            sel_note = f"\n（已附点选上下文：{selection.get('elements', [])}）"
        try:
            rnd = self._client.post_message(self._tune_id, text, selection)
        except PlatformError as exc:
            self._append_note(f"发送失败：{exc.detail}")
            return
        self._append_md(text + sel_note, "user")
        self._round_no = rnd["round_no"]
        self._rendered_tools = 0
        self._round_timer.start()
        self.tune_status.setText(f"r{self._round_no} 思考中…")

    def _on_stop(self) -> None:
        executor.request_stop()
        self._round_timer.stop()
        self.tune_status.setText("已请求停止（当前工具完成后生效）")

    def _poll_round(self) -> None:
        if self._round_no is None or not self._tune_id:
            return
        try:
            rnd = self._client.get_round(self._tune_id, self._round_no)
        except PlatformError:
            return
        trace = rnd.get("llm_trace") or {}
        tools = trace.get("tools") or []
        while self._rendered_tools < len(tools):
            t = tools[self._rendered_tools]
            mark = "✓" if t.get("ok") else "✗"
            self._append_note(f"{mark} 编辑代码 {t.get('code_chars', 0)} 字符 · 质量 {t.get('quality') or t.get('error', '')[:60]}")
            self._rendered_tools += 1
        status = rnd.get("status")
        if status == "running":
            return
        self._round_timer.stop()
        runner = (trace.get("runner") or {})
        summary = runner.get("summary") or rnd.get("error") or ""
        self._append_md(summary, "agent")
        if status == "awaiting_upload":
            self._upload_round(rnd["round_no"])
        elif status == "done":
            self.tune_status.setText("问答完成（无几何变更）")
        elif status == "failed":
            self.tune_status.setText("轮次失败（可重发）")

    def _upload_round(self, round_no: int) -> None:
        """导出 STEP/FCStd 并上传（主线程导出，上传同步执行——v1 从简）。"""
        self.tune_status.setText("导出上传中…")
        try:
            step_path, fcstd_path = self._export_current(round_no)
            resp = self._client.upload_version(self._tune_id, round_no, step_path, fcstd_path)
            version = resp.get("ir_version")
            self._append_note(f"已上传定稿 → 平台 IR3 v{version}（三维预览稍后自动刷新）")
            self.tune_status.setText(f"已上传 IR3 v{version}")
        except PlatformError as exc:
            self._append_note(f"上传失败：{exc.detail}（可在平台侧重试或手工导出）")
            self.tune_status.setText("上传失败")
        except Exception as exc:
            self._append_note(f"导出/上传异常：{type(exc).__name__}: {exc}")
            self.tune_status.setText("上传异常")

    def _export_current(self, round_no: int) -> tuple[str, str]:
        import Import

        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise RuntimeError("没有活动文档")
        # 导出对象：优先 cq_show 的 "Edit" 对象，否则主实体宿主对象
        targets = doc.getObjectsByLabel("Edit")
        if not targets:
            shape = executor.main_model_shape(doc)
            targets = [o for o in doc.Objects if getattr(o, "Shape", None) is shape]
        if not targets:
            raise RuntimeError("文档中没有可导出的形体")
        step_path = tempfile.gettempdir() + f"/sonarlink_r{round_no}.step"
        fcstd_path = tempfile.gettempdir() + f"/sonarlink_r{round_no}.FCStd"
        Import.export(targets, step_path)
        doc.saveCopy(fcstd_path)
        return step_path, fcstd_path

    # ------------------------------------------------------------------
    # 工具调用（主线程执行 FreeCAD API）
    # ------------------------------------------------------------------
    def _on_tool_call(self, call: dict) -> None:
        if call.get("name") != "execute_edit_code":
            self._worker.submit_result(call["call_id"], {"ok": False, "error": f"未知工具 {call.get('name')}"})
            return
        args = call.get("args") or {}
        code = args.get("code", "")
        self._append_note(f"收到编辑指令：{(args.get('description') or '')[:60]}")
        try:
            result = executor.execute_edit(code)
        except Exception as exc:  # 双保险：执行器本身不应抛
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"}
        self._worker.submit_result(call["call_id"], result)

    def _on_conn_note(self, text: str) -> None:
        self._append_note(text)

    # ------------------------------------------------------------------
    # 快照恢复 / 定稿 / 结束
    # ------------------------------------------------------------------
    def _on_restore(self) -> None:
        ok, msg = snapshot_lite.restore()
        if ok:
            executor.reset_session_state()
        self._append_note(msg)

    def _on_finalize(self) -> None:
        if not self._tune_id:
            return
        try:
            self._client.finalize(self._tune_id, note="插件定稿")
            self.tune_status.setText("已定稿（等待审阅关②）")
            self._append_note("微调会话已定稿：审阅关②将审阅最新版本。")
        except PlatformError as exc:
            QtWidgets.QMessageBox.warning(self, "定稿失败", exc.detail)

    def _on_close_tune(self) -> None:
        if not self._tune_id:
            return
        if QtWidgets.QMessageBox.question(self, "结束微调", "放弃当前微调会话？已产生的版本会保留留痕。") != QtWidgets.QMessageBox.Yes:
            return
        try:
            self._client.close(self._tune_id)
            self._worker.set_tune(None)
            self._tune_id = None
            self.tune_status.setText("已结束")
        except PlatformError as exc:
            QtWidgets.QMessageBox.warning(self, "结束失败", exc.detail)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self._worker.stop()
        self._round_timer.stop()
        super().closeEvent(event)


def show_panel() -> AgentPanel:
    panel = getattr(FreeCADGui, "_sonarlink_panel", None)
    if panel is None:
        panel = AgentPanel()
        FreeCADGui._sonarlink_panel = panel
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    panel.raise_()
    return panel
