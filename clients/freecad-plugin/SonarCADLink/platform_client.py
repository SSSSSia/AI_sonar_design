"""SonarCADLink 平台客户端（T8.4，PHASE-008）。

FreeCAD 插件 ↔ 平台后端的全部 HTTP 交互：登录（JWT）/ 拉模型上下文 / 发消息 /
轮次轮询 / 工具调用长轮询 / 结果回传 / 定稿上传。仅用标准库 urllib（FreeCAD 内嵌
Python 环境不装三方依赖）。

插件零凭据原则：本地只保存用户输入的平台地址与登录令牌（内存 + 可选缓存文件），
绝不保存/读取 LLM API key（宪法 §10.1）。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request


class PlatformError(RuntimeError):
    """平台返回的非 2xx（detail 为服务端中文透因）。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class Client:
    """无状态 HTTP 客户端（线程安全：urllib 每请求独立连接）。"""

    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        data = self._request("POST", "/api/v1/auth/login",
                             body={"username": username, "password": password})
        token = (data.get("data") or {}).get("token")
        if not token:
            raise PlatformError(500, "登录响应缺少 token")
        with self._lock:
            self._token = token
        return data.get("data") or {}

    @property
    def token(self) -> str | None:
        with self._lock:
            return self._token

    def logout(self) -> None:
        with self._lock:
            self._token = None

    def _headers(self) -> dict:
        with self._lock:
            token = self._token
        if not token:
            raise PlatformError(401, "尚未登录平台")
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # 微调会话（/api/v1/cad-agent/**）
    # ------------------------------------------------------------------
    def create_tune(self, design_session_id: str) -> dict:
        return self._request("POST", f"/api/v1/cad-agent/sessions/{design_session_id}/tune",
                             headers=self._headers())

    def list_tunes(self, design_session_id: str) -> dict:
        return self._request("GET", f"/api/v1/cad-agent/sessions/{design_session_id}/tune",
                             headers=self._headers())

    def get_tune(self, tune_id: str) -> dict:
        return self._request("GET", f"/api/v1/cad-agent/tune/{tune_id}", headers=self._headers())

    def get_context(self, tune_id: str, version: int | None = None) -> dict:
        url = f"/api/v1/cad-agent/tune/{tune_id}/context"
        if version:
            url += f"?version={version}"
        return self._request("GET", url, headers=self._headers())

    def download_to(self, url: str, dest_path: str) -> str:
        """下载（预签名 URL，不带 Authorization）。返回 dest_path。"""
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout * 4) as resp, open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        return dest_path

    def post_message(self, tune_id: str, text: str, selection: dict | None = None) -> dict:
        return self._request("POST", f"/api/v1/cad-agent/tune/{tune_id}/messages",
                             headers=self._headers(),
                             body={"text": text, "selection": selection or None})

    def get_round(self, tune_id: str, round_no: int) -> dict:
        return self._request("GET", f"/api/v1/cad-agent/tune/{tune_id}/rounds/{round_no}",
                             headers=self._headers())

    def poll_tool_calls(self, tune_id: str, cursor: int, wait: int = 20) -> dict:
        return self._request(
            "GET",
            f"/api/v1/cad-agent/tune/{tune_id}/tool-calls?cursor={cursor}&wait={wait}",
            headers=self._headers(), timeout=self.timeout + wait,
        )

    def post_tool_result(self, tune_id: str, call_id: int, result: dict) -> dict:
        return self._request("POST", f"/api/v1/cad-agent/tune/{tune_id}/tool-calls/{call_id}/result",
                             headers=self._headers(), body=result)

    def upload_version(self, tune_id: str, round_no: int, step_path: str,
                       fcstd_path: str | None = None, script: str = "") -> dict:
        """multipart 上传（标准库手拼 multipart/form-data）。"""
        boundary = "----SonarCADLinkBoundary7d1a2c"
        parts: list[bytes] = []
        parts.append(_form_field(b"round_no", str(round_no), boundary))
        parts.append(_form_field(b"script", script, boundary))
        parts.append(_form_file(b"step_file", step_path, boundary))
        if fcstd_path:
            parts.append(_form_file(b"fcstd_file", fcstd_path, boundary))
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        headers = self._headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return self._request("POST", f"/api/v1/cad-agent/tune/{tune_id}/versions",
                             headers=headers, raw_body=body)

    def finalize(self, tune_id: str, note: str | None = None) -> dict:
        return self._request("POST", f"/api/v1/cad-agent/tune/{tune_id}/finalize",
                             headers=self._headers(), body={"note": note})

    def close(self, tune_id: str) -> dict:
        return self._request("POST", f"/api/v1/cad-agent/tune/{tune_id}/close",
                             headers=self._headers())

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, headers: dict | None = None,
                 body: dict | None = None, raw_body: bytes | None = None,
                 timeout: int | None = None) -> dict:
        url = self.base_url + path
        data = raw_body
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("detail", "")
            except Exception:
                pass
            raise PlatformError(exc.code, detail or str(exc.reason)) from exc
        except urllib.error.URLError as exc:
            raise PlatformError(0, f"无法连接平台（{exc.reason}）——请检查地址与网络") from exc
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            return {"raw": payload.decode("utf-8", errors="replace")}


def _form_field(name: bytes, value: str, boundary: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name.decode()}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _form_file(name: bytes, path: str, boundary: str) -> bytes:
    with open(path, "rb") as f:
        content = f.read()
    filename = path.replace("\\", "/").split("/")[-1]
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name.decode()}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + b"\r\n"
