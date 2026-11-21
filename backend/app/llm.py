"""LiteLLM gateway: ordered key failover (Router), response cache, and the failure-simulation hook."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import litellm
import openai
from litellm import Router
from litellm.caching.caching import Cache
from litellm.caching.in_memory_cache import InMemoryCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.types.router import RouterRateLimitError

from app.config import Settings

log = logging.getLogger("app.llm")

MODEL_GROUP = "chat"
SIMULATION_STATUSES = frozenset({429, 500, 502, 503})
WILDCARD = "*"


@dataclass
class _RequestContext:
    """Per-request state shared with the LiteLLM hook through a ContextVar (no globals)."""

    plan: Mapping[str, int] | None  # simulated failures, {key_id: status}
    attempts: list[str] = field(default_factory=list)  # deployments tried, in order


_request_context: contextvars.ContextVar[_RequestContext | None] = contextvars.ContextVar(
    "llm_request_context", default=None
)


class AllProvidersUnavailableError(Exception):
    """Every deployment in the key pool failed for this request."""


class SimulationHeaderError(ValueError):
    """X-Simulate-Failure could not be parsed."""


def parse_simulation_header(value: str, key_ids: list[str]) -> dict[str, int]:
    """Parse `key_1=429,key_2=500` / `*=429` into a per-key plan.

    A specific key overrides `*`; unknown key ids are ignored.
    """
    wildcard: int | None = None
    specific: dict[str, int] = {}
    for raw in value.split(","):
        pair = raw.strip()
        if not pair:
            continue
        key_id, sep, status_text = (part.strip() for part in pair.partition("="))
        if not sep or not key_id or not status_text.isdigit():
            raise SimulationHeaderError(
                f"invalid X-Simulate-Failure entry {pair!r}: expected <key_id>=<status>, e.g. key_1=429"
            )
        status = int(status_text)
        if status not in SIMULATION_STATUSES:
            allowed = ", ".join(str(s) for s in sorted(SIMULATION_STATUSES))
            raise SimulationHeaderError(f"invalid X-Simulate-Failure status {status}: allowed {allowed}")
        if key_id == WILDCARD:
            wildcard = status
        else:
            specific[key_id] = status

    plan = {key_id: wildcard for key_id in key_ids} if wildcard is not None else {}
    plan.update({k: v for k, v in specific.items() if k in key_ids})
    return plan


def _simulated_error(status: int, model: str) -> Exception:
    message = f"simulated {status} via X-Simulate-Failure"
    args = {"message": message, "llm_provider": "simulated", "model": model}
    match status:
        case 429:
            return litellm.RateLimitError(**args)
        case 500:
            return litellm.InternalServerError(**args)
        case 502:
            return litellm.BadGatewayError(**args)
        case _:
            return litellm.ServiceUnavailableError(**args)


class FailureSimulationHook(CustomLogger):
    """Records each deployment the Router tries and fails the ones listed in the request's plan.

    Runs after the Router has picked a deployment and before the HTTP call, so a raised
    error makes the Router fail over exactly as it would on a real provider error.
    """

    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: Any) -> dict | None:
        ctx = _request_context.get()
        if ctx is None:
            return None
        model_info = kwargs.get("model_info") or (kwargs.get("litellm_params") or {}).get("model_info") or {}
        key_id = str(model_info.get("id", ""))
        ctx.attempts.append(key_id)
        status = ctx.plan.get(key_id) if ctx.plan else None
        if status is not None:
            raise _simulated_error(status, str(kwargs.get("model", "")))
        return None


def _reply_is_non_empty(text: str) -> bool:
    # litellm post-call rule: False raises APIResponseValidationError inside the deployment call,
    # so an empty answer fails over to the next key and is never written to the cache.
    return bool(text and text.strip())


@dataclass(frozen=True)
class CompletionResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int
    cache_hit: bool


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.key_ids = [f"key_{i}" for i in range(1, len(settings.api_keys) + 1)]
        self.models_by_key = dict(zip(self.key_ids, settings.models, strict=True))
        self._configure_litellm()
        self.router = Router(
            model_list=[
                self._deployment(i, key, model)
                for i, (key, model) in enumerate(zip(settings.api_keys, settings.models, strict=True), start=1)
            ],
            num_retries=0,  # failover only; never retry the same key
            disable_cooldowns=True,  # a failure must not change routing for later requests
            cache_responses=True,
        )

    @property
    def keys_configured(self) -> int:
        return len(self.key_ids)

    @property
    def cache_enabled(self) -> bool:
        return litellm.cache is not None

    def _deployment(self, index: int, api_key: str, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "order": index,  # try key_1, then key_2, ...
            "timeout": self.settings.llm_timeout_seconds,
        }
        if self.settings.llm_api_base:
            params["api_base"] = self.settings.llm_api_base
        return {"model_name": MODEL_GROUP, "litellm_params": params, "model_info": {"id": f"key_{index}"}}

    def _configure_litellm(self) -> None:
        litellm.suppress_debug_info = True
        litellm.telemetry = False
        s = self.settings
        if s.cache_type == "disk":
            litellm.cache = Cache(type="disk", disk_cache_dir=s.cache_dir, ttl=s.cache_ttl_seconds)
        else:
            litellm.cache = Cache(type="local", ttl=s.cache_ttl_seconds)
            # The default in-memory cache holds only 200 items for 10 minutes.
            litellm.cache.cache = InMemoryCache(max_size_in_memory=s.cache_max_items, default_ttl=s.cache_ttl_seconds)
        litellm.callbacks = [FailureSimulationHook()]
        litellm.post_call_rules = [_reply_is_non_empty]

    async def complete(self, messages: list[dict[str, str]], simulation: Mapping[str, int] | None) -> CompletionResult:
        kwargs: dict[str, Any] = {}
        if simulation is not None:
            # Always exercise the failover path: never serve a simulated request from cache.
            kwargs["cache"] = {"no-cache": True}
        ctx = _RequestContext(plan=simulation)
        token = _request_context.set(ctx)
        try:
            response = await self.router.acompletion(model=MODEL_GROUP, messages=messages, **kwargs)
        except (openai.APIError, RouterRateLimitError) as exc:
            log.warning("all providers failed: %s", type(exc).__name__)
            raise AllProvidersUnavailableError from exc
        finally:
            _request_context.reset(token)
        return self._to_result(response, ctx.attempts)

    def _to_result(self, response: Any, attempts: list[str]) -> CompletionResult:
        hidden = getattr(response, "_hidden_params", None) or {}
        cache_hit = hidden.get("cache_hit") is True
        deployment_id = hidden.get("model_id")
        model = self.models_by_key.get(deployment_id, self.settings.models[0])
        usage = getattr(response, "usage", None)
        # LiteLLM's x-litellm-attempted-fallbacks header caps at 1 with 3+ keys (1.110.2),
        # so count the deployments our hook saw instead; the header is only a fallback.
        header = (hidden.get("additional_headers") or {}).get("x-litellm-attempted-fallbacks") or 0
        attempted = len(attempts) - 1 if attempts else int(header)
        return CompletionResult(
            reply=response.choices[0].message.content.strip(),
            model=model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            key_id=None if cache_hit else deployment_id,
            fallback_attempts=0 if cache_hit else int(attempted),
            cache_hit=cache_hit,
        )

    async def warm_up(self) -> None:
        """Exercise the cache-hit code path once so the first real hit is fast.

        LiteLLM imports a lot of modules lazily on the hit path (~300 ms the first time).
        This also fails startup loudly if the image lacks a dependency the hit path needs.
        """
        request = {
            "model": "openai/cache-warmup",
            "messages": [{"role": "user", "content": "cache warm-up"}],
            "mock_response": "ok",
            "api_key": "warmup",
            "caching": True,
        }
        await litellm.acompletion(**request)
        for _ in range(50):  # the cache write is asynchronous
            response = await litellm.acompletion(**request)
            if (response._hidden_params or {}).get("cache_hit") is True:
                log.info("cache warm-up done")
                return
            await asyncio.sleep(0.02)
        log.warning("cache warm-up did not observe a cache hit")
