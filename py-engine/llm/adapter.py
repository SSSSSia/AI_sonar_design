"""统一 LLM 适配器（T1.2，D-12）。

职责：
- 屏蔽供应商差异：统一走 OpenAI 兼容 /chat/completions 协议（目标形态私有化 vLLM，
  开发期公网 API fallback，P3 §4.7 允许）；
- 多供应商热切换：优先读业务库 llm_providers（带 TTL 热加载，改库即生效），
  库中无 enabled 行时回退 .env 环境供应商；
- 密钥安全：ProviderConfig 用 SecretStr，模型序列化/日志输出一律脱敏（宪法 §10.2）；
- 有界终止：调用失败重试上限 LLM_MAX_RETRIES（A-13），超限抛异常如实上抛。

不做：提示词模板管理（T3.3）、并发限流细化（随 A1 接入时补）。
"""

from __future__ import annotations

import json
import random
import logging
import threading
import time
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from app_config import (
    DATABASE_URL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_TIMEOUT_S,
    PROVIDER_CACHE_TTL_S,
)

logger = logging.getLogger("sonarcad.llm")


class ProviderConfig(BaseModel):
    """一个 LLM 供应商配置。api_key 为 SecretStr——任何输出必须走 model_dump(redacted)。"""

    name: str
    base_url: str
    api_key: SecretStr
    model: str
    timeout_s: int = 60

    def redacted(self) -> dict[str, Any]:
        """对外（API/日志/报告）唯一合法的输出形式：api_key 脱敏。"""
        d = self.model_dump()
        d["api_key"] = "****"
        return d


def _load_providers_from_db() -> list[ProviderConfig] | None:
    """业务库读取 enabled 供应商；表不可用返回 None（=查询失败，抖动保护）；
    无 enabled 行返回 []（=确认切换为空，走 env 兜底/MISSING）。两者语义不同，T10.3 修订。"""
    try:
        import psycopg

        with psycopg.connect(DATABASE_URL.replace("+psycopg", "")) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT name, base_url, api_key, model, timeout_s
                   FROM llm_providers WHERE enabled ORDER BY priority DESC"""
            )
            rows = cur.fetchall()
        return [
            ProviderConfig(name=r[0], base_url=r[1], api_key=r[2], model=r[3], timeout_s=r[4])
            for r in rows
        ]
    except Exception as exc:  # 连接失败不阻断：降级 env 供应商并如实记录
        logger.warning("llm_providers 读取失败，回退 env 供应商: %s", exc)
        return None


class _ProviderStore:
    """带 TTL 的供应商缓存（热切换：DB 改动后 ≤TTL 生效）。"""

    def __init__(self, ttl_s: int) -> None:
        self._ttl_s = ttl_s
        self._cached_at = 0.0
        self._providers: list[ProviderConfig] = []
        self._lock = threading.Lock()

    def _env_fallback(self) -> list[ProviderConfig]:
        if LLM_BASE_URL and LLM_API_KEY:
            return [
                ProviderConfig(
                    name="env-fallback",
                    base_url=LLM_BASE_URL,
                    api_key=LLM_API_KEY,
                    model=LLM_MODEL,
                    timeout_s=LLM_TIMEOUT_S,
                )
            ]
        return []

    def active(self) -> ProviderConfig:
        with self._lock:
            if time.time() - self._cached_at > self._ttl_s:
                providers = _load_providers_from_db()
                if providers is not None:
                    # 查询成功：以 DB 为准——空列表 = 确认无 enabled 行，如实切换
                    # （env 兜底 / MISSING）。T10.3 修订：旧逻辑把「确认无行」当抖动
                    # 保留旧供应商，管理面禁用最后一家供应商永不生效（实测踩坑）。
                    self._providers = providers or self._env_fallback()
                # providers is None（查询失败）= DB 抖动：保留旧值不切换
                self._cached_at = time.time()
            return self._providers[0] if self._providers else _MISSING

    def chain(self) -> list:
        """按优先级排序的全部可用供应商（T11.6 失败转移链，首元素 = active）。"""
        with self._lock:
            return list(self._providers)


_PROVIDER_HEALTH: dict = {}
_HEALTH_LOCK = threading.Lock()


def _record_health(name: str, ok: bool, error: str | None = None) -> None:
    """供应商最近一次调用结果（T11.6：/llm/provider 健康可见）。"""
    import time as _time

    with _HEALTH_LOCK:
        _PROVIDER_HEALTH[name] = {
            "ok": ok,
            "last_error": (error or "")[:200] if not ok else None,
            "checked_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        }


def provider_health() -> list:
    """各供应商最近健康态（管理面展示）。"""
    with _HEALTH_LOCK:
        return [{"name": name, **info} for name, info in _PROVIDER_HEALTH.items()]


class _MissingProvider:
    def __str__(self) -> str:
        return "MISSING_PROVIDER"


_MISSING = _MissingProvider()

_store = _ProviderStore(PROVIDER_CACHE_TTL_S)


def active_provider() -> ProviderConfig:
    """当前生效供应商（热切换入口）。无可用供应商时抛错，不静默。"""
    p = _store.active()
    if isinstance(p, _MissingProvider):
        raise RuntimeError("无可用 LLM 供应商：业务库 llm_providers 无 enabled 行且 .env 未配置")
    return p


def reload_providers() -> None:
    """强制立即重载（测试/管理用）。供应商配置变更 → L1 的 LLM 缓存一并失效（T9.6）。"""
    from storage import cache

    _store._cached_at = 0.0
    cache.invalidate_llm()


class LlmAdapter:
    """OpenAI 兼容 chat completion 客户端（流式 + L1 缓存边界）。

    流式原因（旧仓库同款踩坑）：长脚本生成（A3 可达数千 token）非流式等待整包
    极易触发读超时；流式下读超时按 chunk 间隔计，连接持续有数据即不超时。

    L1 缓存（T9.6）：chat() 边界整体生效——实现上虽走流式，但结果聚合为完整字符串返回
    （无 SSE 透传），故「流式不走缓存」的顾虑不成立，缓存覆盖全部调用；
    键 = sha256(model+messages+temperature 等 kwargs)，TTL 7d，LLM_CACHE_ENABLED 可关。
    """

    def chat(self, messages: list[dict[str, str]], timeout_s: int | None = None, **kwargs: Any) -> str:
        """timeout_s：单次调用超时覆盖（默认用供应商配置）；其余 kwargs 透传请求体。

        T11.6：出站消息先经 llm/sanitize 键值脱敏（默认开——密钥/token/密码/连接串）；
        供应商失败转移——主供应商重试耗尽后按优先级次高继续（LLM_FAILOVER 缺省开）。
        """
        from app_config import LLM_CACHE_ENABLED
        from llm.sanitize import sanitize_messages
        from storage import cache

        messages, _hits = sanitize_messages(messages)  # 出站脱敏（缓存键随之基于脱敏后内容）

        cache_key: str | None = None
        if LLM_CACHE_ENABLED:
            try:
                provider = active_provider()
                # 供应商标识入键（T9.6 修订）：mock/换供应商场景跨实例命中旧响应
                # 会让 e2e 不可重复（实测教训）——同 base_url+model 才命中
                tag = f"{provider.base_url}|{provider.model}"
            except RuntimeError:
                provider = None  # 无可用供应商时本次调用本就会失败，跳过缓存
                tag = ""
            if provider:
                cache_key = cache.llm_cache_key(tag, messages, kwargs)
                cached = cache.get_str("llm", cache_key)
                if cached is not None:
                    return cached

        chain: list = []
        if _FAILOVER_ENABLED:
            chain = [p for p in _store.chain() if not isinstance(p, _MissingProvider)]
        if not chain:
            chain = [active_provider()]
        last_exc: Exception | None = None
        for provider in chain:
            outcome = self._chat_single(provider, messages, timeout_s, cache_key, kwargs)
            if isinstance(outcome, str):
                return outcome
            last_exc = outcome
        raise RuntimeError(f"LLM 调用在全部 {len(chain)} 家供应商（含各自重试）后仍失败") from last_exc

    def _chat_single(self, provider, messages, timeout_s, cache_key, kwargs):
        """单供应商调用（含 LLM_MAX_RETRIES 重试）。成功返回 str；最终失败返回最后异常。"""
        from storage import cache

        last_exc: Exception | None = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            t0 = time.perf_counter()
            try:
                chunks: list[str] = []
                with httpx.stream(
                    "POST",
                    f"{provider.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {provider.api_key.get_secret_value()}"},
                    json={
                        "model": provider.model,
                        "messages": messages,
                        "stream": True,
                        **kwargs,
                    },
                    timeout=httpx.Timeout(connect=30, read=timeout_s or provider.timeout_s, write=30, pool=30),
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload in ("", "[DONE]"):
                            continue
                        choices = json.loads(payload).get("choices") or []
                        if not choices:  # 部分供应商会发 usage-only 空块
                            continue
                        content = choices[0].get("delta", {}).get("content")
                        if content:
                            chunks.append(content)
                result = "".join(chunks)
                _observe_llm_call(provider.name, time.perf_counter() - t0, True)
                _record_health(provider.name, True)
                if cache_key and result:
                    cache.set_str(cache_key, result, cache.TTL_LLM_S)
                return result
            except Exception as exc:
                _observe_llm_call(provider.name, time.perf_counter() - t0, False)
                last_exc = exc
                logger.warning(
                    "LLM 调用失败(第 %s/%s 次, provider=%s): %s",
                    attempt, LLM_MAX_RETRIES, provider.name, exc,
                )
                # T12.1（P2-6）：退避分级——429/5xx/网络错误指数退避后重试；
                # 其余 4xx（鉴权/参数类）重试无意义，直接失败进失败转移链。
                if attempt < LLM_MAX_RETRIES and _should_backoff(exc):
                    delay = min(LLM_BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, LLM_BACKOFF_BASE_S * 0.2),
                                LLM_BACKOFF_MAX_S)
                    logger.info("LLM 退避 %.1fs 后重试（provider=%s）", delay, provider.name)
                    time.sleep(delay)
                    continue
                if attempt < LLM_MAX_RETRIES:
                    logger.info("LLM 客户端错误不退避重试（provider=%s）", provider.name)
                break
        _record_health(provider.name, False, str(last_exc) if last_exc else "unknown")
        return last_exc


def _observe_llm_call(provider_name: str, duration_s: float, ok: bool) -> None:
    """单次尝试耗时/成败打点（T10.1a；缓存命中路径不在此列——那不是 LLM 调用）。"""
    try:
        from observability.metrics import observe_llm

        observe_llm(provider_name, duration_s, ok)
    except Exception:
        pass  # 指标故障不影响调用主链路


def _should_backoff(exc: Exception) -> bool:
    """T12.1（P2-6）：仅显式 4xx 客户端错误（401 鉴权/422 参数等，429 除外）不重试——
    重试无意义且拖慢转移链；429/5xx/网络/未知错误一律指数退避后重试（保持既有
    「transient 可重试」语义，P2-6 只新增节律）。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return True


def _env_flag(name: str, default: str) -> bool:
    import os

    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no")


_FAILOVER_ENABLED = _env_flag("LLM_FAILOVER", "1")


def _env_float(name: str, default: str) -> float:
    import os

    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


LLM_BACKOFF_BASE_S = _env_float("LLM_BACKOFF_BASE_S", "2")
LLM_BACKOFF_MAX_S = _env_float("LLM_BACKOFF_MAX_S", "30")

llm_adapter = LlmAdapter()
