# -*- coding: utf-8 -*-
"""T8.4 插件协议单测（无 FreeCAD 环境）：platform_client 的登录/会话/长轮询/上传/下载。

用线程内 http.server 起 stub 平台，校验请求形态与错误透传；FreeCAD 相关模块
（executor/panel）走 README 真机人工验收清单。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from platform_client import Client, PlatformError


class _StubHandler(BaseHTTPRequestHandler):
    received: list[tuple] = []  # (method, path, headers, body_bytes)

    def log_message(self, *args):  # 静默
        pass

    def _reply(self, code: int, payload: dict | bytes, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).received.append(("POST", self.path, dict(self.headers), body))
        if self.path == "/api/v1/auth/login":
            creds = json.loads(body)
            if creds.get("password") != "right":
                self._reply(401, {"detail": "用户名或密码错误"})
            else:
                self._reply(200, {"data": {"token": "t-123", "user": {"name": creds["username"]}}})
        elif self.path.endswith("/messages"):
            assert self.headers.get("Authorization") == "Bearer t-123"
            self._reply(200, {"round_no": 1, "status": "running"})
        elif self.path.endswith("/result"):
            self._reply(200, {"status": "done"})
        elif self.path.endswith("/versions"):
            # multipart 解包校验：round_no 字段 + step_file 文件名 + 二进制体
            assert "multipart/form-data; boundary=" in (self.headers.get("Content-Type") or "")
            raw = body
            assert b'name="round_no"' in raw and b"\r\n7\r\n" in raw or b'name="round_no"' in raw
            assert b'name="step_file"' in raw and b'filename="m.step"' in raw
            assert b"STEP-BYTES" in raw
            self._reply(200, {"ir_version": 2, "geometry_summary": {"source": "extracted"}})
        else:
            self._reply(404, {"detail": "not found"})

    def do_GET(self):
        type(self).received.append(("GET", self.path, dict(self.headers), b""))
        if self.path.startswith("/api/v1/workflow/sessions"):
            assert self.headers.get("Authorization") == "Bearer t-123"
            assert "page=1" in self.path and "size=100" in self.path
            self._reply(200, {"code": 0, "message": "ok", "traceId": "x", "data": {
                "total": 2, "records": [
                    {"id": "a" * 32, "title": "拖曳阵设计", "projectName": "P1",
                     "pipelineStatus": "paused", "currentStage": "gate2",
                     "tuneId": None, "tuneStatus": None},
                    {"id": "b" * 32, "title": "舷侧阵", "projectName": None,
                     "pipelineStatus": "running", "currentStage": "A3",
                     "tuneId": "t-9", "tuneStatus": "active"},
                ]}})
        elif self.path.startswith("/api/v1/cad-agent/tune/tid/tool-calls"):
            assert "cursor=0" in self.path and "wait=5" in self.path
            self._reply(200, {"calls": [{"call_id": 7, "name": "execute_edit_code",
                                         "args": {"code": "result = model"}}], "cursor": 7})
        elif self.path.startswith("/files/step"):
            self._reply(200, b"ISO-10303-21;", content_type="application/step")
        else:
            self._reply(404, {"detail": "not found"})


@pytest.fixture(scope="module")
def base_url():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_login_and_token_gate(base_url):
    c = Client(base_url)
    with pytest.raises(PlatformError) as ei:
        c.list_tunes("sid")  # 未登录
    assert ei.value.status_code == 401

    with pytest.raises(PlatformError) as ei:
        c.login("admin", "wrong")
    assert ei.value.status_code == 401 and "密码" in ei.value.detail

    data = c.login("admin", "right")
    assert c.token == "t-123"
    assert data["user"]["name"] == "admin"


def test_message_and_round_flow(base_url):
    c = Client(base_url)
    c.login("admin", "right")
    rnd = c.post_message("tid", "加一圈法兰", selection={"kind": "Face"})
    assert rnd["round_no"] == 1
    method, path, headers, body = _StubHandler.received[-1]
    payload = json.loads(body)
    assert payload == {"text": "加一圈法兰", "selection": {"kind": "Face"}}
    assert headers["Authorization"] == "Bearer t-123"


def test_tool_call_poll_and_result(base_url):
    c = Client(base_url)
    c.login("admin", "right")
    resp = c.poll_tool_calls("tid", cursor=0, wait=5)
    assert resp["cursor"] == 7
    assert resp["calls"][0]["args"]["code"] == "result = model"
    ok = c.post_tool_result("tid", 7, {"ok": True, "quality": {"status": "pass"}})
    assert ok["status"] == "done"


def test_upload_version_multipart(base_url, tmp_path):
    c = Client(base_url)
    c.login("admin", "right")
    step = tmp_path / "m.step"
    step.write_bytes(b"STEP-BYTES")
    fcstd = tmp_path / "m.FCStd"
    fcstd.write_bytes(b"FCSTD-BYTES")
    resp = c.upload_version("tid", round_no=3, step_path=str(step), fcstd_path=str(fcstd), script="result = model")
    assert resp["ir_version"] == 2
    _, _, _, body = _StubHandler.received[-1]
    assert b'name="fcstd_file"' in body and b"FCSTD-BYTES" in body
    assert "result = model".encode() in body


def test_list_sessions_envelope(base_url):
    """会话选择器数据源：ApiResponse 包络正确解出 records（panel 侧再做可微调过滤）。"""
    c = Client(base_url)
    c.login("admin", "right")
    rows = c.list_sessions()
    assert len(rows) == 2
    assert rows[0]["id"] == "a" * 32 and rows[0]["currentStage"] == "gate2"
    assert rows[1]["tuneStatus"] == "active"
    method, path, headers, _ = _StubHandler.received[-1]
    assert (method, path) == ("GET", "/api/v1/workflow/sessions?page=1&size=100")


def test_download_and_error_detail(base_url, tmp_path):
    c = Client(base_url)
    c.login("admin", "right")
    dest = tmp_path / "model.step"
    c.download_to(f"{base_url}/files/step", str(dest))
    assert dest.read_bytes() == b"ISO-10303-21;"

    with pytest.raises(PlatformError) as ei:
        c._request("GET", "/nope", headers=c._headers())
    assert ei.value.status_code == 404 and ei.value.detail == "not found"
