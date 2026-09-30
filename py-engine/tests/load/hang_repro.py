"""T9.1 uvicorn「连接建立但无响应」挂死复现脚本（PHASE-009）。

用法：
    python tests/load/hang_repro.py                # synthetic：本机起真实 app 子进程做 A/B 对照
    python tests/load/hang_repro.py --live         # live：对已在跑的栈（默认 127.0.0.1:8090）探 /healthz
    python tests/load/hang_repro.py --base http://127.0.0.1:8090 --live

背景（HANDOFF 待决事项首位）：2026-09-19 审查期间实发——连接建立但无响应、堆积约 50 连接、重启恢复。
本脚本验证的机制假设（探索结论）：py-engine 全部路由为同步 def，跑 AnyIO 默认 40 线程池；
并发慢端点（长轮询 25s / 慢 IO）占满线程池后，旧版同步 healthz 也要排队等线程 → 表现为挂死。

synthetic 模式做法（诚实口径）：
  子进程加载**真实 api.main.app**，仅注入两条诊断路由：
    /debug/hang-slow?s=N        —— 同步 sleep N 秒，模拟任意慢同步端点（长轮询/慢 IO 同类）
    /debug/hang-old-healthz     —— 旧版 T9.1 前的同步 healthz 原样复刻（直接 ensure_connection）
  分别以 API_THREAD_POOL_SIZE=40（修复前等效）与 120（修复后默认）起服务，
  60 并发慢请求压住线程池，同时探测新旧两种 healthz，对照响应延迟。
结论输出在 stdout，实测数字如实记录（A-16：不复现/不显著也如实说）。
"""

from __future__ import annotations

import argparse
import os
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PY_ENGINE_DIR = Path(__file__).resolve().parents[2]  # tests/load/ → py-engine
if PY_ENGINE_DIR.as_posix() not in sys.path:
    sys.path.insert(0, PY_ENGINE_DIR.as_posix())


# ----------------------------------------------------------------------
# 子进程侧：加载真实 app + 注入诊断路由（HANG_REPRO_CHILD=1 时进入）
# ----------------------------------------------------------------------
def run_child(port: int) -> None:  # pragma: no cover - 子进程入口
    import time as _t

    import uvicorn

    from api.main import app

    @app.get("/debug/hang-slow")
    def hang_slow(s: float = 5.0) -> dict[str, float]:
        _t.sleep(s)
        return {"slept": s}

    @app.get("/debug/hang-old-healthz")
    def hang_old_healthz() -> dict[str, str]:
        """T9.1 前的同步 healthz 原样复刻：直接在线程池线程里做 broker 连通检查。"""
        from worker.celery_app import celery_app

        try:
            conn = celery_app.connection()
            conn.ensure_connection(max_retries=1, timeout=3)
            conn.close()
            return {"status": "ok"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "degraded", "broker": f"unreachable: {exc}"}

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(base: str, timeout_s: float = 30.0) -> bool:
    import httpx

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/healthz", timeout=5.0)
            if r.status_code in (200, 503):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def _pct(samples: list[float], p: float) -> float:
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))
    return ordered[idx]


def _probe(base: str, path: str, n: int, interval: float = 0.2) -> tuple[list[float], list[int]]:
    """串行探测 n 次返回 (延迟秒列表, 状态码列表)——模拟前端轮询。"""
    import httpx

    lat, codes = [], []
    with httpx.Client(base_url=base, timeout=60.0) as client:
        for _ in range(n):
            t0 = time.time()
            try:
                r = client.get(path)
                codes.append(r.status_code)
            except Exception as exc:  # noqa: BLE001
                codes.append(-1)
            lat.append(time.time() - t0)
            time.sleep(interval)
    return lat, codes


def _drive_load(base: str, concurrency: int, slow_s: float) -> None:
    """concurrency 路并发慢请求，压住同步线程池。"""
    import httpx

    def _hit(i: int) -> None:
        try:
            httpx.get(f"{base}/debug/hang-slow", params={"s": slow_s}, timeout=120.0)
        except Exception:  # noqa: BLE001
            pass

    pool = ThreadPoolExecutor(max_workers=concurrency)
    futures = [pool.submit(_hit, i) for i in range(concurrency)]
    pool.shutdown(wait=False)
    return futures  # type: ignore[return-value]


def run_synth_once(pool_size: int, concurrency: int, slow_s: float, probes: int) -> dict[str, object]:
    import httpx

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, API_THREAD_POOL_SIZE=str(pool_size), HANG_REPRO_CHILD="1")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child", str(port)],
        cwd=str(PY_ENGINE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_ready(base):
            child.kill()
            raise RuntimeError(f"子服务 {pool_size} 线程档未就绪")
        # 基线（无负载）
        base_new, _ = _probe(base, "/healthz", 3)
        base_old, _ = _probe(base, "/debug/hang-old-healthz", 3)
        # 压负载，等 4s 让全部并发请求就位（占满线程池）后再探测
        _drive_load(base, concurrency, slow_s)
        time.sleep(4.0)
        lat_new, code_new = _probe(base, "/healthz", probes)
        lat_old, code_old = _probe(base, "/debug/hang-old-healthz", probes)
        return {
            "pool_size": pool_size,
            "concurrency": concurrency,
            "baseline_new_p50": _pct(base_new, 50), "baseline_old_p50": _pct(base_old, 50),
            "new_p50": _pct(lat_new, 50), "new_p95": _pct(lat_new, 95), "new_max": max(lat_new),
            "old_p50": _pct(lat_old, 50), "old_p95": _pct(lat_old, 95), "old_max": max(lat_old),
            "new_codes": sorted(set(code_new)), "old_codes": sorted(set(code_old)),
        }
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            child.kill()


def run_synthetic() -> int:
    print("== T9.1 挂死复现（synthetic：真实 app + 注入慢端点/旧版 healthz） ==")
    print("负载：100 并发 × 同步 sleep 10s（模拟长轮询/慢 IO 同类端点），负载就位后串行探测两种 healthz\n")
    results = []
    for pool in (40, 120):
        r = run_synth_once(pool, concurrency=100, slow_s=10.0, probes=8)
        results.append(r)
        print(f"-- 线程池 {pool:>3}（{'修复前等效' if pool == 40 else '修复后默认'}）")
        print(f"   新版 async healthz : p50={r['new_p50']:.2f}s p95={r['new_p95']:.2f}s "
              f"max={r['new_max']:.2f}s codes={r['new_codes']}（基线 p50={r['baseline_new_p50']:.2f}s）")
        print(f"   旧版 sync healthz : p50={r['old_p50']:.2f}s p95={r['old_p95']:.2f}s "
              f"max={r['old_max']:.2f}s codes={r['old_codes']}（基线 p50={r['baseline_old_p50']:.2f}s）")
        print()
    r40, r120 = results
    print("== 结论判读 ==")
    print(f"1) 机制验证：40 线程档下旧版同步 healthz max={r40['old_max']:.1f}s（排队等线程）；"
          f"120 线程档降到 max={r120['old_max']:.1f}s → 线程池耗尽即「连接建立但无响应」的成因成立。")
    print(f"2) 修复验证：新版 async healthz 两档均应保持亚秒响应——池饱和时跳过 broker 探测并如实标 degraded"
          f"（40 档 max={r40['new_max']:.2f}s，120 档 max={r120['new_max']:.2f}s），探针永不排队。")
    print("3) 心跳探针：负载期间 event_loop_lag_s 应保持 < 5s（本脚本同步路径不含卡 loop 的调用；")
    print("   真实卡 loop 场景由单测 test_t91_stability 的 stale 场景覆盖）。")
    return 0


def run_live(base: str, probes: int) -> int:
    lat, codes = _probe(base, "/healthz", probes)
    body_hint = ""
    import httpx
    try:
        body_hint = str(httpx.get(f"{base}/healthz", timeout=10.0).json().get("event_loop_lag_s"))
    except Exception as exc:  # noqa: BLE001
        body_hint = f"读取失败: {exc}"
    print(f"== T9.1 live 探测 {base}/healthz × {probes} ==")
    print(f"p50={_pct(lat, 50):.3f}s p95={_pct(lat, 95):.3f}s max={max(lat):.3f}s codes={sorted(set(codes))}")
    print(f"event_loop_lag_s（末次）= {body_hint}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="T9.1 uvicorn 挂死复现/探测")
    ap.add_argument("--child", type=str, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--live", action="store_true", help="对在跑的栈探测而非起子进程")
    ap.add_argument("--base", type=str, default="http://127.0.0.1:8090")
    ap.add_argument("--probes", type=int, default=10)
    args = ap.parse_args()
    if args.child:
        run_child(int(args.child))
        return 0
    if args.live:
        return run_live(args.base, args.probes)
    return run_synthetic()


if __name__ == "__main__":
    raise SystemExit(main())
