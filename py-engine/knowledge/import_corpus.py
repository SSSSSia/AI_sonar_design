"""批量导入文献资料到知识库（T3.3 后续，HTTP 驱动——复用运行中的 api+worker）。

流程（逐文件）：上传解析 → 分块 → 异步抽取 → 确认全部 pending → 统一三层构建 → 检索验证。

用法（py-engine 目录，api+worker 在跑）：
    python -m knowledge.import_corpus 文献1.txt 文献2.txt
    python -m knowledge.import_corpus --auto-confirm false 文献1.txt   # 抽取后不自动确认（人工审）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8090"
AUTH = {"X-User-Id": "corpus-import", "X-User-Roles": "admin"}


def _wait_task(c: httpx.Client, kind: str, task_id: str, deadline_s: float) -> dict:
    deadline = time.time() + deadline_s
    url = f"/api/v1/kb/{kind}/{task_id}"
    while time.time() < deadline:
        r = c.get(url)
        data = r.json()
        if data.get("state") == "SUCCESS":
            return data["result"]
        if data.get("state") == "FAILED":
            raise RuntimeError(f"{kind} 任务失败: {data.get('error')}")
        time.sleep(5)
    raise TimeoutError(f"{kind} 任务超时（{deadline_s:.0f}s）")


def import_file(c: httpx.Client, path: Path, auto_confirm: bool = True) -> str:
    data = path.read_bytes()
    r = c.post("/api/v1/kb/documents",
               files={"file": (path.name, data, "text/plain")})
    r.raise_for_status()
    body = r.json()
    if body.get("status") == "failed":
        raise RuntimeError(f"解析失败: {body.get('error')}")
    doc_id = body["doc_id"]
    print(f"  [1/4] 解析完成 {doc_id}: {body['units']} 单元（ocr_pending={body.get('ocr_pending', 0)}）")

    r = c.post(f"/api/v1/kb/documents/{doc_id}/chunk", json={})
    r.raise_for_status()
    print(f"  [2/4] 分块: {r.json()['chunks']} chunks")

    r = c.post(f"/api/v1/kb/documents/{doc_id}/extract", json={})
    r.raise_for_status()
    result = _wait_task(c, "extract", r.json()["task_id"], 1800)
    print(f"  [3/4] 抽取: {result['extractions']} 条")

    if auto_confirm:
        r = c.get(f"/api/v1/kb/documents/{doc_id}/extractions", params={"status": "pending"})
        pend = r.json()["extractions"]
        for item in pend:
            r2 = c.patch(f"/api/v1/kb/extractions/{item['id']}", json={"status": "confirmed"})
            r2.raise_for_status()
        print(f"        已自动确认 {len(pend)} 条（种子文献可信；可经预览 API 复审）")
    return doc_id


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    auto_confirm = "--auto-confirm=false" not in sys.argv
    if not args:
        print('用法: python -m knowledge.import_corpus [--auto-confirm=false] 文献1.txt [文献2.txt ...]')
        sys.exit(1)
    paths = [Path(a) for a in args]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"文件不存在: {missing}")
        sys.exit(1)

    started = time.time()
    doc_ids: list[str] = []
    with httpx.Client(base_url=BASE, headers=AUTH, timeout=300) as c:
        for p in paths:
            print(f"导入 {p.name}（{p.stat().st_size} 字节）")
            doc_ids.append(import_file(c, p, auto_confirm))

        t0 = time.time()
        r = c.post("/api/v1/kb/build", json={"doc_ids": doc_ids})
        result = _wait_task(c, "build", r.json()["task_id"], 900)
        print(f"[4/4] 三层构建: indexed={result['indexed']}/{result['total']}"
              f"（{time.time() - t0:.0f}s）")
        for d in result["documents"]:
            print(f"       {d['doc_id']}: {d.get('detail')}")

        # 检索验证（期望 graph/vector 级命中）
        for q in ("声呐方程 指向性指数", "水声传播损失", "拖曳声呐 总体设计"):
            r = c.post("/knowledge/search", json={"query": q})
            body = r.json()
            print(f"检索「{q}」→ level={body['level']} degraded={body['degraded_from']}")

    print(f"\n导入完成：{len(doc_ids)} 文档，总耗时 {time.time() - started:.0f}s")
    print(json.dumps({"doc_ids": doc_ids}, ensure_ascii=False))


if __name__ == "__main__":
    main()
