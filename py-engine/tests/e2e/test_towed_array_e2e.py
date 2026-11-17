# -*- coding: utf-8 -*-
"""T1.7 端到端冒烟（宪法 §9.3）：自然语言需求 → MQ/状态机 → IR0–IR3 快照 + STEP 落 MinIO。

前置（由运行方保证，缺失则 skip 并给出原因）：
- 数据底座 compose 全部 healthy（PG/Redis/RabbitMQ/MinIO）
- Java 四服务已启动（gateway 8080 / auth 8081 / workflow 8082 / biz 8083）
- py-engine worker 与 api(8090) 已启动
- mock LLM 已注册为 llm_providers 中 enabled 且 priority 最高的一行
  （真实 key 就绪后删除该行即走真实供应商，本测试无需改动）

运行（py-engine 目录）：python -m pytest tests/e2e/test_towed_array_e2e.py -v
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GATEWAY = "http://127.0.0.1:8080"
PYAPI = "http://127.0.0.1:8090"
PG_DSN = "postgresql://{user}:{pw}@{host}:{port}/{db}"

STANDARD_REQUIREMENT = (
    "设计一个拖曳线列阵声呐，16 个水听器阵元，阵元间距 1.5 米，最大工作深度 300 米。"
)


def _pg_dsn() -> str:
    from app_config import DATABASE_URL

    return DATABASE_URL.replace("+psycopg", "")


def _pg_exec(sql: str, fetch: bool = False):
    import psycopg

    with psycopg.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def _stack_ready() -> tuple[bool, str]:
    for name, url in (
        ("gateway", f"{GATEWAY}/api/v1/auth/login"),
        ("py-engine", f"{PYAPI}/healthz"),
    ):
        try:
            httpx.post(url, json={}, timeout=5) if "login" in url else httpx.get(url, timeout=5)
        except Exception as exc:
            return False, f"{name} 不可达: {exc}"
    rows = _pg_exec(
        "SELECT count(*) FROM llm_providers WHERE enabled AND priority = (SELECT max(priority) FROM llm_providers)",
        fetch=True,
    )
    if rows and rows[0][0] > 0:
        return True, ""
    # 表空时允许 .env 真实供应商兜底（D-12），占位符不算
    from app_config import LLM_API_KEY, LLM_BASE_URL

    if LLM_BASE_URL and LLM_API_KEY and not LLM_API_KEY.startswith("your_api"):
        return True, ""
    return False, "无可用 LLM 供应商（llm_providers 空且 .env 未配置真实 key）"


@pytest.fixture(scope="module")
def stack():
    ok, reason = _stack_ready()
    if not ok:
        pytest.skip(f"e2e 前置不满足：{reason}")
    yield


def _login() -> str:
    r = httpx.post(
        f"{GATEWAY}/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def _cleanup(headers, sid=None, pid=None):
    """成功后清理本 run 的会话与项目（失败不清理，保留现场排查）。

    会话强删走 workflow 级联（快照/审阅/溯源 + MinIO 产物；已沉淀知识保留）；
    项目删除要求其下无会话，故顺序为先会话后项目。清理失败只打印不 assert。
    """
    if sid:
        r = httpx.delete(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers,
                         params={"force": "true"}, timeout=120)
        print(f"cleanup session -> {r.status_code}")
    if pid:
        r = httpx.delete(f"{GATEWAY}/api/v1/projects/{pid}", headers=headers, timeout=15)
        print(f"cleanup project -> {r.status_code}")


def test_towed_array_full_chain(stack):
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    run_tag = f"e2e-{uuid.uuid4().hex[:8]}"

    # 1. 建项目 + 带需求的会话（IR0 固化）
    r = httpx.post(f"{GATEWAY}/api/v1/projects", headers=headers,
                   json={"name": run_tag, "code": run_tag}, timeout=15)
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]

    r = httpx.post(
        f"{GATEWAY}/api/v1/workflow/sessions",
        headers=headers,
        json={"projectId": pid, "title": "拖曳线列阵标准样例", "requirement": STANDARD_REQUIREMENT},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["data"]["id"]

    # 2. START → 编排层驱动固定管线 A1→A2→A3
    r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/events", headers=headers,
                   json={"event": "START"}, timeout=15)
    assert r.status_code == 200 and r.json()["data"]["pipelineStatus"] == "running", r.text

    # 3. 轮询到终态（上限 25 分钟：真实 LLM 时 A1–A3 共 4~8 次调用，公网延迟波动大）
    # 阶段 2 起管线带审阅关（A2 后 gate1、A4 后 gate2）：到达暂停即批准，门语义见 PHASE-002 §2.1
    deadline = time.time() + 1500

    def _state() -> dict:
        r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=10)
        return r.json()["data"]

    def _approve(gate: str) -> None:
        r = httpx.post(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews/{gate}",
                       headers=headers, json={"decision": "approved",
                                              "annotations": ["T1.7 e2e 自动过闸"]},
                       timeout=15)
        assert r.status_code == 200, r.text

    final = None
    while time.time() < deadline:
        data = _state()
        status = data["pipelineStatus"]
        if status == "paused" and data.get("currentStage") in ("gate1", "gate2"):
            _approve(data["currentStage"])
            continue
        if status in ("completed", "failed"):
            final = status
            break
        time.sleep(6)
    assert final == "completed", f"会话未在时限内完成（final={final}）"

    # 4. IR0–IR3 快照齐全且 created_by 正确
    expected_creator = {"IR0": "user", "IR1": "A1", "IR2": "A2", "IR3": "A3"}
    contents = {}
    for ir_type, creator in expected_creator.items():
        r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/{ir_type}", headers=headers, timeout=10)
        assert r.status_code == 200, f"{ir_type} 快照缺失: {r.text}"
        data = r.json()["data"]
        assert data["created_by"] == creator, f"{ir_type} created_by={data['created_by']}"
        contents[ir_type] = data["content"] if isinstance(data["content"], dict) else json.loads(data["content"])

    # 5. IR2 契约核心断言：四件套齐全 + 公式参数数值可复算（A-06/A-07）
    for p in contents["IR2"]["parameters"]:
        assert p["trace_id"].startswith("tr-"), f"{p['id']} 缺 trace_id"
        assert p["source_type"] in ("formula", "case", "standard", "estimated")
        assert str(p["source_ref"]).strip() and str(p["derivation"]).strip()
    formulas = [p for p in contents["IR2"]["parameters"] if p["source_type"] == "formula"]
    assert formulas, "IR2 缺少公式类参数（A-07：公式数值必须由代码计算）"
    import math
    import re

    di = next((p for p in formulas if "指向性" in p["name"]), None)
    assert di is not None, f"缺少指向性指数公式参数，现有: {[p['name'] for p in formulas]}"
    m = re.search(r"n=([\d.]+)", di["derivation"])
    assert m, "DI derivation 未记录输入 n"
    assert abs(di["value"] - 10 * math.log10(float(m.group(1)))) < 1e-3, "公式参数数值必须可由 derivation 复算"

    # 6. IR3 + STEP 产物在 MinIO（D-10 分桶）真实可读
    step_ref = contents["IR3"]["step_file"]
    assert step_ref["bucket"] and step_ref["object_key"]
    from minio import Minio
    from app_config import MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY

    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)
    stat = client.stat_object(step_ref["bucket"], step_ref["object_key"])
    assert stat.size > 1000, f"STEP 过小: {stat.size}"

    # STEP 可被 OCC 重新读入（几何有效性抽查）
    tmp = Path("tests/e2e/_verify.step")
    client.fget_object(step_ref["bucket"], step_ref["object_key"], str(tmp))
    import cadquery as cq

    shape = cq.importers.importStep(str(tmp))
    assert shape.val().Volume() > 0
    tmp.unlink()

    # 6.5 PHASE-007（T7.5 验收 3）：IR3 特征真实化 + A4 数值复核断言
    summary = contents["IR3"].get("geometry_summary") or {}
    assert summary.get("source") == "extracted", f"IR3 非实测提取: {summary}"
    feats = contents["IR3"]["features"]
    assert len(feats) >= 1 and summary.get("part_count") == len(feats), \
        f"features 数应等于实测段数: part_count={summary.get('part_count')} features={len(feats)}"
    assert any(f.get("linked_param_id") for f in feats), "至少一个长度类参数应自动联动"

    # 6.6 T15.1/T15.2（PHASE-015，ADR-014）：@display 显示变换声明 + 声明制参数→对象映射
    dt = contents["IR3"].get("display_transforms") or []
    assert len(dt) >= 1, f"IR3 应携带 @display 显示变换声明: {dt}"
    pm = contents["IR3"].get("param_mapping") or {}
    assert pm.get("identity_basis") == "declared_objects" and pm.get("objects"), \
        f"IR3 应携带声明制对象映射: {pm}"
    verified = [o for o in pm["objects"] if o.get("status") == "verified"]
    assert verified, f"至少一个声明对象应 verified: {pm}"
    arr = next((o for o in verified if o.get("object_id") == "array_section"), None)
    assert arr is not None, f"array_section 应 verified: {pm}"
    meas = {p["dim_type"]: p for p in arr["params"]}
    assert meas["diameter"]["measurement"] == "radius_derived", f"孔径证据应为圆度半径: {meas}"
    assert abs(meas["diameter"]["measured_mm"] - 27.0) < 0.5, f"外径实测应≈27mm: {meas}"
    assert abs(meas["length"]["measured_mm"] - 24000.0) < 1.0, f"总长实测应≈24000mm: {meas}"
    # T15.5 壁厚闭环：声阵段为真实薄壁管（外径 27/内径 21），wall_derived 推导+内孔面
    # 互证自证 → 实测≈3mm（与 IR2 P-002「声阵段壁厚 3mm」精确一致）
    assert meas["thickness"]["measurement"] == "wall_derived", f"壁厚证据应为环形推导: {meas}"
    assert abs(meas["thickness"]["measured_mm"] - 3.0) < 0.5, f"壁厚实测应≈3mm: {meas}"

    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/irs/IR4", headers=headers, timeout=10)
    assert r.status_code == 200, f"IR4 快照缺失: {r.text}"
    ir4 = r.json()["data"]["content"]
    ir4 = ir4 if isinstance(ir4, dict) else json.loads(ir4)
    codes = [i["constraint_id"] for i in ir4["issues"]]
    # T15.3 后检查景观：被映射覆盖的参数（P-001/P-002/P-004）由语义检查器核对（legacy 数值
    # 复核排除避免双重裁决）；mock 声明覆盖下长度/孔径/壁厚均有语义判定
    assert ("CHK-GEO-NUM" in codes) or ("CHK-SEM-LENGTH" in codes), \
        f"IR4 缺少数值/语义复核条目: {codes}"
    assert "CHK-SEM-LENGTH" in codes and "CHK-SEM-DIAMETER" in codes, \
        f"IR4 应含首批语义检查器判定（长度/孔径）: {codes}"
    assert "CHK-SEM-THICKNESS" in codes, f"T15.5 壁厚语义判定应入报告: {codes}"
    assert "CHK-SEM-MAPPING" not in codes, f"声明绑定应全链一致无歧义: {codes}"
    assert "CHK-DISPLAY-TRANSFORM" in codes, f"IR4 应显性呈现显示变换声明: {codes}"
    assert "CHK-GEO-NUM-SKIP" not in codes, "新会话不得命中数值复核跳过分支"

    # 7. 会话 stage=A5（管线走满五棒：A3 之后 A4 校验、gate2 批准、A5 沉淀）
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}", headers=headers, timeout=10)
    assert r.json()["data"]["currentStage"] == "A5"

    # 8. 批准组合与正式导出（T15.6/C-0/C-1，ADR-014 §5）：gate2 批准记录携带组合凭证；
    #    正式导出按组合读取并校验（不读"当时 latest"），工作稿/正式版文件名分别标注
    r = httpx.get(f"{GATEWAY}/api/v1/workflow/sessions/{sid}/reviews", headers=headers, timeout=10)
    records = r.json()["data"]
    gate2_ok = [x for x in records if x["gate"] == "gate2" and x["decision"] == "approved"]
    assert gate2_ok, f"应有 gate2 批准记录: {[(x['gate'], x['decision']) for x in records]}"
    metadata = json.loads(gate2_ok[0].get("metadata") or "{}")
    combo = metadata.get("snapshot_combo") or {}
    assert set(combo.keys()) >= {"IR2", "IR3", "IR4"}, f"组合应含 IR2/IR3/IR4: {list(combo)}"
    for t, entry in combo.items():
        assert entry.get("version"), f"{t} 组合条目缺版本"
        assert entry.get("snapshot_id"), f"{t} 组合条目缺快照ID"
        assert entry.get("content_sha256") and len(entry["content_sha256"]) == 64, \
            f"{t} 组合条目缺 sha256（口径=content::text）"
    policy = metadata.get("combo_policy") or {}
    assert policy.get("ir4_overall") in ("pass", "fail", "incomplete"), \
        f"combo_policy 应含 ir4_overall: {policy}"
    assert isinstance(policy.get("unresolved"), list), "combo_policy 应含 unresolved 列表"

    from urllib.parse import unquote

    record_id = gate2_ok[0]["id"]
    r = httpx.get(f"{GATEWAY}/api/v1/cad/sessions/{sid}/export/docx",
                  params={"record_id": record_id}, headers=headers, timeout=120)
    assert r.status_code == 200, f"正式导出应通过组合校验: {r.status_code} {r.text[:200]}"
    assert "正式批准" in unquote(r.headers.get("content-disposition", "")), "正式版文件名应标注"
    r = httpx.get(f"{GATEWAY}/api/v1/cad/sessions/{sid}/export/docx", headers=headers, timeout=120)
    assert r.status_code == 200, "工作稿导出不应回归"
    assert "工作稿" in unquote(r.headers.get("content-disposition", "")), "工作稿文件名应标注"
    r = httpx.get(f"{GATEWAY}/api/v1/cad/sessions/{sid}/export/docx",
                  params={"record_id": "00000000-0000-0000-0000-000000000000"},
                  headers=headers, timeout=30)
    assert r.status_code == 404, f"伪造批准记录应 404: {r.status_code}"

    # 9. B3 候选沉淀（T16.3，ADR-015 v1.1 §9）：真实 gate2 批准 → 候选可见 → 沉淀 pending
    #    → 重复事件幂等不重复入库。沉淀行不清理（同内容 ref_id=dep-{sha8} 幂等单行自洽，
    #    反复运行天然验证「重复事件不重复入库」；pending 不参与检索，人工可确认或停用）
    r = httpx.get(f"{GATEWAY}/api/v1/kb/references/deposit-candidates",
                  params={"limit": 100}, headers=headers, timeout=30)
    assert r.status_code == 200, f"沉淀候选扫描应 200: {r.text[:200]}"
    scan = r.json()
    # 会话 id 归一（Java API 出参无连字符，PG uuid::text 带连字符——内容相等比对）
    norm = lambda v: str(v or "").replace("-", "")
    cand = next((c for c in scan["candidates"] if norm(c["session_id"]) == norm(sid)), None)
    assert cand is not None, f"本会话 gate2 批准应出现在可沉淀候选: sid={sid}"
    assert norm(cand["record_id"]) == norm(record_id), f"候选应绑定本批准记录: {cand}"
    assert not any(norm(b["session_id"]) == norm(sid) for b in scan["blocked"]), \
        f"组合有效且质量门通过的会话不应进 blocked: {scan['blocked'][:3]}"

    r = httpx.post(f"{GATEWAY}/api/v1/kb/references/deposit", headers=headers,
                   json={"session_id": norm(sid), "record_id": norm(record_id)}, timeout=300)
    assert r.status_code == 200, f"沉淀应通过判定链+重放: {r.status_code} {r.text[:300]}"
    dep = r.json()
    if not dep["already"]:
        row = dep["row"]
        assert row["status"] == "pending", f"沉淀行应 pending（A-16 只产候选）: {row}"
        assert row["source_type"] == "deposit", f"来源应为 deposit: {row}"
        note = json.loads(row["source_note"])
        assert norm(note["session_id"]) == norm(sid), f"凭证应绑定来源: {note}"
        assert norm(note["record_id"]) == norm(record_id), f"凭证应绑定批准记录: {note}"
        assert note["ir3_version"] == combo["IR3"]["version"], f"凭证应绑定确切 IR3 版本: {note}"
        assert note["quality"]["replay"] == "pass", f"凭证应记录重放通过: {note}"
    row_id = dep["row"]["id"]

    r = httpx.post(f"{GATEWAY}/api/v1/kb/references/deposit", headers=headers,
                   json={"session_id": norm(sid), "record_id": norm(record_id)}, timeout=300)
    assert r.status_code == 200 and r.json()["already"] is True, \
        f"重复沉淀应幂等返回既有行: {r.text[:200]}"
    assert r.json()["row"]["id"] == row_id, "重复事件应命中同一行（不重复入库）"

    print(f"\ne2e 通过：session={sid} run={run_tag} step={step_ref}")
    _cleanup(headers, sid, pid)
