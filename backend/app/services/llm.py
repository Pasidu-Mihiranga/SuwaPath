"""Multi-provider LLM router with automatic failover.

SuwaPath has no budget for a paid model, so it cannot depend on any single
free tier staying up. Three providers are tried in order and each one is
independently allowed to fail:

    1. Groq         — fastest by a wide margin on free tiers (~150-400ms).
    2. OpenRouter   — free model slugs; slower and more often rate-limited.
    3. Gemini       — kept because the project was built against it, but free
                      keys are frequently provisioned with ``limit: 0``.
    4. (none)       — every caller has a deterministic composer to fall back
                      on. A missing model degrades wording, never safety.

A provider that fails is put on a short cooldown rather than being retried on
every request, so one dead key does not add its timeout to every turn. The
cooldown is deliberately short (60s) because free-tier failures are usually
per-minute rate limits that clear on their own.

Every call returns a ``Completion`` carrying which provider answered and how
long it took. That is surfaced in the UI — an assistant that quietly changes
model underneath the user should at least say so.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]

# Free tiers rate-limit per minute far more often than they hard-fail, so a
# failed provider is retried a minute later rather than being written off.
_COOLDOWN_SECONDS = 60.0
_TIMEOUT_SECONDS = 45.0


@dataclass(slots=True)
class Completion:
    """One model response, or an empty one when every provider declined."""

    text: str
    provider: str
    model: str
    latency_ms: int
    ok: bool = True
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok and bool(self.text.strip())


class _Health:
    """Per-provider cooldown tracking, safe across LangGraph's worker threads."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def available(self, provider: str) -> bool:
        with self._lock:
            return time.monotonic() >= self._until.get(provider, 0.0)

    def penalise(self, provider: str) -> None:
        with self._lock:
            self._until[provider] = time.monotonic() + _COOLDOWN_SECONDS

    def clear(self, provider: str) -> None:
        with self._lock:
            self._until.pop(provider, None)

    def snapshot(self) -> dict[str, float]:
        now = time.monotonic()
        with self._lock:
            return {
                name: round(until - now, 1)
                for name, until in self._until.items()
                if until > now
            }


_health = _Health()

# One pooled client. Creating a client per call costs a TCP + TLS handshake
# each time, which on a chat turn is the difference between 200ms and 600ms.
_http = httpx.Client(timeout=_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
def _openai_style(
    *,
    url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    as_json: bool,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Groq and OpenRouter both speak the OpenAI chat-completions dialect."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if as_json:
        payload["response_format"] = {"type": "json_object"}
        # Groq rejects json_object mode unless the word "json" appears in the
        # conversation. Rather than make every caller remember that, the
        # requirement is satisfied here.
        if not any("json" in m["content"].lower() for m in messages):
            messages = [*messages, {"role": "user", "content": "Reply with JSON only."}]
            payload["messages"] = messages

    response = _http.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", **(extra_headers or {})},
    )
    response.raise_for_status()
    data = response.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices in response: {str(data)[:200]}")
    message = choices[0].get("message") or {}
    # Reasoning models put the user-visible answer in `content` and their
    # scratchpad in `reasoning`. An empty `content` means the model spent its
    # whole budget thinking, which is a failure for our purposes.
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError("empty content (model returned only reasoning)")
    return text


def _call_groq(messages, temperature, max_tokens, as_json, fast) -> tuple[str, str]:
    model = settings.groq_fast_model if fast else settings.groq_model
    return model, _openai_style(
        url="https://api.groq.com/openai/v1/chat/completions",
        api_key=settings.groq_api_key or "",
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        as_json=as_json,
    )


def _call_open_router(messages, temperature, max_tokens, as_json, fast) -> tuple[str, str]:
    model = settings.open_router_model
    return model, _openai_style(
        url="https://openrouter.ai/api/v1/chat/completions",
        api_key=settings.open_router_api_key or "",
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        as_json=as_json,
        # OpenRouter attributes free-tier usage to a referring app.
        extra_headers={
            "HTTP-Referer": "https://suwapath.lk",
            "X-Title": "SuwaPath",
        },
    )


def _call_gemini(messages, temperature, max_tokens, as_json, fast) -> tuple[str, str]:
    model = settings.gemini_model
    # Gemini has no system role: the system message is prepended to the first
    # user turn, which is what the official cookbook recommends.
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for message in messages:
        if message["role"] == "system":
            continue
        text = message["content"]
        if system and not contents:
            text = f"{system}\n\n{text}"
        contents.append({
            "role": "model" if message["role"] == "assistant" else "user",
            "parts": [{"text": text}],
        })

    config: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if as_json:
        config["responseMimeType"] = "application/json"

    response = _http.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        json={"contents": contents, "generationConfig": config},
        headers={"x-goog-api-key": settings.gemini_api_key or ""},
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"no candidates: {str(data)[:200]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise RuntimeError("empty candidate text")
    return model, text


_PROVIDERS: tuple[tuple[str, Any, str], ...] = (
    ("groq", _call_groq, "groq_enabled"),
    ("openrouter", _call_open_router, "open_router_enabled"),
    ("gemini", _call_gemini, "gemini_enabled"),
)


# --------------------------------------------------------------------------
# Public interface
# --------------------------------------------------------------------------
def complete(
    messages: list[dict[str, str]] | str,
    *,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 900,
    as_json: bool = False,
    fast: bool = False,
) -> Completion:
    """Ask the first healthy provider. Never raises.

    ``fast=True`` asks for the small model where a provider has one — used for
    routing and classification, where latency matters more than nuance.
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if system:
        messages = [{"role": "system", "content": system}, *messages]

    started = time.perf_counter()
    attempts: list[dict[str, Any]] = []

    for name, call, flag in _PROVIDERS:
        if not getattr(settings, flag):
            continue
        if not _health.available(name):
            attempts.append({"provider": name, "status": "cooling_down"})
            continue

        attempt_started = time.perf_counter()
        try:
            model, text = call(messages, temperature, max_tokens, as_json, fast)
        except Exception as exc:  # noqa: BLE001
            # A 400 means *we* sent something the provider disliked. Cooling
            # the provider down would punish it for our bug and push every
            # subsequent request to a slower fallback, so only genuine
            # unavailability (rate limits, auth, outages) earns a cooldown.
            if _is_our_fault(exc):
                logger.error("LLM request rejected by %s: %s", name, _short_error(exc))
            else:
                _health.penalise(name)
                logger.warning("LLM provider %s unavailable: %s", name, _short_error(exc))
            attempts.append({
                "provider": name,
                "status": "error",
                "detail": _short_error(exc),
                "ms": int((time.perf_counter() - attempt_started) * 1000),
            })
            continue

        _health.clear(name)
        attempts.append({
            "provider": name,
            "status": "ok",
            "ms": int((time.perf_counter() - attempt_started) * 1000),
        })
        return Completion(
            text=text,
            provider=name,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
        )

    return Completion(
        text="",
        provider="none",
        model="deterministic",
        latency_ms=int((time.perf_counter() - started) * 1000),
        ok=False,
        attempts=attempts,
    )


def complete_json(
    messages: list[dict[str, str]] | str,
    *,
    system: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 900,
    fast: bool = False,
) -> tuple[dict | None, Completion]:
    """``complete`` plus tolerant JSON parsing. Returns ``(data, completion)``."""
    completion = complete(
        messages,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        as_json=True,
        fast=fast,
    )
    return parse_json(completion.text), completion


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(raw: str | None) -> dict | None:
    """Parse JSON that a model may have wrapped in prose or a code fence."""
    if not raw:
        return None
    text = raw.strip()

    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    # Last resort: the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _is_our_fault(exc: Exception) -> bool:
    """True for a malformed request, as opposed to provider unavailability."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 400
    )


def _short_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:160].replace("\n", " ")
        return f"HTTP {exc.response.status_code}: {body}"
    return f"{type(exc).__name__}: {exc}"[:200]


def available() -> bool:
    """True when at least one provider is configured and not cooling down."""
    return any(
        getattr(settings, flag) and _health.available(name)
        for name, _, flag in _PROVIDERS
    )


def status() -> dict:
    """Provider configuration and live health, for /agent/status."""
    return {
        "providers": [
            {
                "name": name,
                "configured": bool(getattr(settings, flag)),
                "healthy": getattr(settings, flag) and _health.available(name),
                "model": {
                    "groq": settings.groq_model,
                    "openrouter": settings.open_router_model,
                    "gemini": settings.gemini_model,
                }[name],
            }
            for name, _, flag in _PROVIDERS
        ],
        "order": [name for name, _, _ in _PROVIDERS],
        "cooling_down": _health.snapshot(),
        "any_available": available(),
    }
