# -*- coding: utf-8 -*-
"""U3.13 全量清理脚本（用户授权 2026-10-11）：级联删除全部项目（含会话）+ 孤儿会话。

流程：
1. 登录拿 token（经网关 8080）
2. 逐项目：GET 其会话 → DELETE /sessions?ids=...&force=true → DELETE /projects/{id}
3. 清完项目后：列出剩余会话（孤儿）→ 分批 DELETE（force）
4. 校验：projects total / sessions total 归零，输出汇总
ASCII 输出（Git Bash GBK 规避）；429/5xx 指数退避重试。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"


def req(method: str, path: str, token: str | None = None, body: dict | None = None, retries: int = 4):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (2 ** attempt))
                continue
            err = e.read().decode(errors="replace")[:200]
            raise RuntimeError(f"{method} {path} -> {e.code}: {err}")
    raise RuntimeError(f"{method} {path} exhausted retries")


def main():
    tok = req("POST", "/auth/login", body={"username": "admin", "password": "admin123"})["data"]["token"]
    print("[login] ok")

    # 1) 取全量项目
    projects, page = [], 1
    while True:
        d = req("GET", f"/projects?page={page}&size=500", tok)["data"]
        projects.extend(d["records"])
        if len(projects) >= d.get("total", 0) or not d["records"]:
            break
        page += 1
    print(f"[projects] total={len(projects)}")

    # 2) 逐项目级联清理
    ok_p = fail_p = sessions_removed = 0
    t0 = time.time()
    for i, p in enumerate(projects, 1):
        try:
            sids, pg = [], 1
            while True:
                d = req("GET", f"/workflow/sessions?projectId={p['id']}&page={pg}&size=500", tok)["data"]
                sids.extend(r["id"] for r in d["records"])
                if len(sids) >= d.get("total", 0) or not d["records"]:
                    break
                pg += 1
            for j in range(0, len(sids), 50):
                chunk = sids[j:j + 50]
                q = urllib.parse.urlencode({"ids": ",".join(chunk), "force": "true"})
                res = req("DELETE", f"/workflow/sessions?{q}", tok)["data"]
                sessions_removed += res.get("deleted_count", 0)
            req("DELETE", f"/projects/{p['id']}", tok)
            ok_p += 1
        except Exception as e:  # noqa: BLE001
            fail_p += 1
            print(f"  [fail] {p.get('name', p['id'])}: {e}")
        if i % 50 == 0:
            print(f"  [progress] {i}/{len(projects)} ({time.time() - t0:.0f}s) ok={ok_p} fail={fail_p} sessions={sessions_removed}")
    print(f"[projects] done ok={ok_p} fail={fail_p} sessions_removed={sessions_removed} in {time.time() - t0:.0f}s")

    # 3) 孤儿会话（无项目归属的残留）
    remaining, pg = [], 1
    while True:
        d = req("GET", f"/workflow/sessions?page={pg}&size=500", tok)["data"]
        remaining.extend(r["id"] for r in d["records"])
        if len(remaining) >= d.get("total", 0) or not d["records"]:
            break
        pg += 1
    print(f"[orphan-sessions] {len(remaining)}")
    orphan_deleted = 0
    for j in range(0, len(remaining), 50):
        chunk = remaining[j:j + 50]
        q = urllib.parse.urlencode({"ids": ",".join(chunk), "force": "true"})
        res = req("DELETE", f"/workflow/sessions?{q}", tok)["data"]
        orphan_deleted += res.get("deleted_count", 0)
    print(f"[orphan-sessions] deleted={orphan_deleted}")

    # 4) 校验
    pt = req("GET", "/projects?size=1", tok)["data"].get("total")
    st = req("GET", "/workflow/sessions?size=1", tok)["data"].get("total")
    print(f"[verify] projects_total={pt} sessions_total={st}")
    if pt or st:
        sys.exit(2)


if __name__ == "__main__":
    main()
