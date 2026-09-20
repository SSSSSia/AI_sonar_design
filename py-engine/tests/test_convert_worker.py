# -*- coding: utf-8 -*-
"""convert_worker 单测：converting 状态的超时判定（纯函数）+ 键布局。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from convert_worker import STALE_SECONDS, glb_key, is_stale, status_key


def test_is_stale_converting_fresh():
    assert is_stale({"state": "converting", "ts": time.time()}, time.time()) is False


def test_is_stale_converting_overdue():
    now = time.time()
    assert is_stale({"state": "converting", "ts": now - STALE_SECONDS - 1}, now) is True


def test_is_stale_never_for_non_converting():
    now = time.time()
    assert is_stale(None, now) is False
    assert is_stale({"state": "failed", "detail": "x", "ts": now - 9999}, now) is False
    assert is_stale({}, now) is False


def test_is_stale_missing_ts_treated_as_stale():
    # ts 缺失/类型异常：无法证明仍在转，宁可如实判中断让用户重试（A-16）
    assert is_stale({"state": "converting"}, time.time()) is True
    assert is_stale({"state": "converting", "ts": "bad"}, time.time()) is True


def test_key_layout():
    assert glb_key("s1", 2) == "s1/2.glb"
    assert status_key("s1", 2) == "s1/2.status"
