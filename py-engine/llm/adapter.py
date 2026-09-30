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
    """业务库读取 enabled 供应商；表不可用/无行返回 None（走 env 兜底）。"""
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
        ] or None
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

    def active(self) -> ProviderConfig:
        with self._lock:
            if time.time() - self._cached_at > self._ttl_s:
                providers = _load_providers_from_db()
                if providers:
                    self._providers = providers
                elif not self._providers:
                    # DB 无可用行且无历史供应商时才走 env 兜底；已有供应商保持不变（DB 抖动不切换）
                    if LLM_BASE_URL and LLM_API_KEY:
                        self._providers = [
                            ProviderConfig(
                                name="env-fallback",
                                base_url=LLM_BASE_URL,
                                api_key=LLM_API_KEY,
                                model=LLM_MODEL,
                                timeout_s=LLM_TIMEOUT_S,
                            )
                        ]
                    else:
                        self._providers = []
                self._cached_at = time.time()
            return self._providers[0] if self._providers else _MISSING


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
        """timeout_s：单次调用超时覆盖（默认用供应商配置）；其余 kwargs 透传请求体。"""
        from app_config import LLM_CACHE_ENABLED
        from storage import cache

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

        last_exc: Exception | None = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            provider = active_provider()
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
        raise RuntimeError(f"LLM 调用在 {LLM_MAX_RETRIES} 次重试后仍失败") from last_exc


def _observe_llm_call(provider_name: str, duration_s: float, ok: bool) -> None:
    """单次尝试耗时/成败打点（T10.1a；缓存命中路径不在此列——那不是 LLM 调用）。"""
    try:
        from observability.metrics import observe_llm

        observe_llm(provider_name, duration_s, ok)
    except Exception:
        pass  # 指标故障不影响调用主链路


llm_adapter = LlmAdapter()
