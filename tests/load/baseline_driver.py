"""T10.3 性能基线驱动（PHASE-010）：复刻 towed array 标准样例全链路，采集阶段耗时。

与 e2e 的差异：本脚本**不删除会话**（stage_duration 行随会话级联删，必须先采集后保留），
直接从 session_logs（kind='stage_duration'）取各阶段耗时并输出 JSON 摘要。

用法（py-engine 目录，栈已起）：
    python ../tests/load/baseline_driver.py --mode real --tag round-1
    python ../tests/load/baseline_driver.py --mode mock --tag mock-1

--mode real：禁用 mock provider（走 .env 兜底真实供应商）；mock：启用 priority=1000 行。
端到端口径：POST START → 轮询见 completed 的墙钟（含每道闸 ~一个轮询间隔的批复延迟，
真实人工思考时间不在本口径内，报告已如实标注）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "py-engine"))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"
DEADLINE_S = 1800
POLL_S = 6

STANDARD_REQUIREMENT = (
    "设计一个拖曳线列阵声呐，16 个水听器阵元，阵元间距 1.5 米，最大工作深度 300 米。"
)


def _dsn() -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "py-engine"))
    from app_config import DATABASE_URL

    return DATABASE_URL.replace("+psycopg", "")


def toggle_provider(mode: str) -> None:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("UPDATE llm_providers SET enabled = %s WHERE name = 'mock-loadtest'",
                    (mode == "mock",))
        conn.commit()
    r = httpx.post(f"{PYAPI}/llm/provider/reload", timeout=10)
    print(f"provider -> {mode} ({r.status_code}), active:", end=" ")
    print(httpx.get(f"{PYAPI}/llm/provider", timeout=10).json().get("name"))


def stage_rows(token: str, sid: str) -> list[dict]:
    r = httpx.get(f"{GATEWAY}/api/v1/logs/sessions/{sid}",
                  headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    out = []
    for row in r.json()["logs"]:
        if row.get("kind") == "stage_duration":
            text = row["text"]
            stage = text.split("]")[1].strip().split()[0]  # "[计时] A1 阶段耗时 12.5s（成功）"
            dur = float(text.split("耗时 ")[1].split("s")[0])
            out.append({"stage": stage, "duration_s": dur, "text": text})
    return out


def run(mode: str, tag: str) -> dict:
    toggle_provider(mode)
    headers = {}
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   json={"username": "admin", "password": "admin123"}, timeout=15)
    token = r.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    run_tag = f"baseline-{tag}-{uuid.uuid4().hex[:6]}"
    r = httpx.post(f"{GATEWAY}/api/v1/projects", headers=headers,
                   json={"name": run_tag, "code": run_tag}, timeout=15)
    r.raise_for_status()
    pid = r.json()["data"]["id"]
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions", headers=headers,
                   json={"projectId": pid, "title": ("基线" + tag)[-20:], "requirement": STANDARD_REQUIREMENT},
                   timeout=15)
    r.raise_for_status()
    sid = r.json()["data"]["id"]

    t0 = time.time()
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/events",
                   headers=headers, json={"event": "START"}, timeout=15)
    r.raise_for_status()
    assert r.json()["data"]["pipelineStatus"] == "running", r.text

    final = None
    approved: set[str] = set()
    while time.time() - t0 < DEADLINE_S:
        data = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=15).json()["data"]
        status, stage = data["pipelineStatus"], data.get("currentStage")
        if status == "paused" and stage in ("gate1", "gate2", "knowledge") and stage not in approved:
            approved.add(stage)
            rr = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/{stage}",
                            headers=headers, json={"decision": "approved",
                                                   "annotations": ["T10.3 基线自动过闸"]}, timeout=15)
            rr.raise_for_status()
            continue
        if status in ("completed", "failed"):
            final = status
            break
        time.sleep(POLL_S)
    wall_s = round(time.time() - t0, 1)

    stages = stage_rows(token, sid)
    csr = None
    ir4 = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/IR4", headers=headers, timeout=15).json()["data"]
    if ir4:
        content = ir4["content"]
        content = json.loads(content) if isinstance(content, str) else content
        csr = content.get("csr")

    summary = {
        "mode": mode, "tag": tag, "session_id": sid, "project_id": pid,
        "final": final, "wall_start_to_complete_s": wall_s,
        "stages": stages,
        "stage_sum": round(sum(s["duration_s"] for s in stages), 1),
        "ir4_csr": csr,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "mock"], required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    run(args.mode, args.tag)
