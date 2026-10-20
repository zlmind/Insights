"""AI usage metering: capture token usage and estimated cost for paid API calls.

Imported by ``insights.py`` right after each paid OpenAI / Anthropic / Ollama call. Each recorder
reads the response's ``usage`` block, estimates a cost in yuan (元) from the local price
table below, resolves whether the call happened *proactively* (automated background
work) or *reactively* (a user-triggered web request), and writes a row via
``database.log_usage``.

Design rules
------------
* Recording MUST NEVER raise into generation code. Every public recorder swallows its
  own exceptions and logs a warning instead. ``insights.py`` also calls these through a
  guarded ``_meter`` helper, so even an import failure here cannot break generation.
* Prices are ESTIMATES, in yuan per 1M tokens. Providers do not return a money amount,
  so cost is computed from ``PRICING`` / the module constants below, each overridable
  via a ``USAGE_PRICE_*`` (or ``USAGE_WHISPER_PER_MIN``) environment variable.
  Token counts, by contrast, are exact.
* Ollama / local generation is recorded at ¥0 (tokens still captured) so local vs cloud
  spend stays visible.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── Pricing (元 CNY) ───────────────────────────────────────────────────────
# Chat rates are yuan per 1,000,000 tokens (input / output). These are estimates;
# tune them to your plan via the matching USAGE_PRICE_* env vars. The OpenAI and
# Claude defaults below are their USD list prices converted at 7.2 (2026-10);
# DeepSeek's are DeepSeek's own yuan list prices.

def _envf(name: str, default: float) -> float:
    """Return env var ``name`` as a float, falling back to ``default``."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return float(default)


# Keys are matched by longest-prefix against the model name, so e.g.
# "gpt-4o-2024-08-06" resolves to the "gpt-4o" entry and "gpt-4o-mini-..." to
# "gpt-4o-mini".
PRICING = {
    # DeepSeek, official yuan list prices (api-docs.deepseek.com pricing).
    # Cached-input tokens are billed here at the cache-miss rate, so runs with
    # many cache hits estimate slightly high.
    "deepseek-v4-pro": {
        "in": _envf("USAGE_PRICE_DEEPSEEK_V4_PRO_IN", 3.00),
        "out": _envf("USAGE_PRICE_DEEPSEEK_V4_PRO_OUT", 6.00),
    },
    "deepseek-v4-flash": {
        "in": _envf("USAGE_PRICE_DEEPSEEK_V4_FLASH_IN", 1.00),
        "out": _envf("USAGE_PRICE_DEEPSEEK_V4_FLASH_OUT", 2.00),
    },
    # The API's own alias: responses echo model="deepseek-flash".
    "deepseek-flash": {
        "in": _envf("USAGE_PRICE_DEEPSEEK_FLASH_IN", 1.00),
        "out": _envf("USAGE_PRICE_DEEPSEEK_FLASH_OUT", 2.00),
    },
    # GPT-6 family (USD list prices as published 2026-09-29, converted at 7.2;
    # prompts over 272K tokens on Astra bill at a higher rate, which this
    # estimate ignores).
    "gpt-6-astra": {
        "in": _envf("USAGE_PRICE_GPT_6_ASTRA_IN", 72.00),
        "out": _envf("USAGE_PRICE_GPT_6_ASTRA_OUT", 360.00),
    },
    "gpt-6.1-sol": {
        "in": _envf("USAGE_PRICE_GPT_6_SOL_IN", 14.40),
        "out": _envf("USAGE_PRICE_GPT_6_SOL_OUT", 72.00),
    },
    "gpt-6-sol": {
        "in": _envf("USAGE_PRICE_GPT_6_SOL_IN", 14.40),
        "out": _envf("USAGE_PRICE_GPT_6_SOL_OUT", 72.00),
    },
    "gpt-6-luna": {
        "in": _envf("USAGE_PRICE_GPT_6_LUNA_IN", 0.72),
        "out": _envf("USAGE_PRICE_GPT_6_LUNA_OUT", 3.60),
    },
    "gpt-4o-mini": {
        "in": _envf("USAGE_PRICE_GPT_4O_MINI_IN", 1.08),
        "out": _envf("USAGE_PRICE_GPT_4O_MINI_OUT", 4.32),
    },
    "gpt-4o": {
        "in": _envf("USAGE_PRICE_GPT_4O_IN", 18.00),
        "out": _envf("USAGE_PRICE_GPT_4O_OUT", 72.00),
    },
    "gpt-4.1-mini": {
        "in": _envf("USAGE_PRICE_GPT_41_MINI_IN", 2.88),
        "out": _envf("USAGE_PRICE_GPT_41_MINI_OUT", 11.52),
    },
    "gpt-4.1": {
        "in": _envf("USAGE_PRICE_GPT_41_IN", 14.40),
        "out": _envf("USAGE_PRICE_GPT_41_OUT", 57.60),
    },
}

# Used for any model name not matched above (assume gpt-4o-class pricing).
DEFAULT_PRICE = {
    "in": _envf("USAGE_PRICE_DEFAULT_IN", 18.00),
    "out": _envf("USAGE_PRICE_DEFAULT_OUT", 72.00),
}

# Anthropic (Claude) chat rates, yuan per 1,000,000 tokens (input / output),
# USD list prices converted at 7.2. Matched by longest-prefix against the model
# name, so e.g. "claude-opus-4-8" resolves to the "claude-opus" entry unless a
# more specific key is added. Opus 5.5 and the Sonnet 5.x line are priced below
# their older siblings, so they need their own keys; Fable sits above Opus.
PRICING_ANTHROPIC = {
    "claude-fable": {
        "in": _envf("USAGE_PRICE_CLAUDE_FABLE_IN", 72.00),
        "out": _envf("USAGE_PRICE_CLAUDE_FABLE_OUT", 360.00),
    },
    "claude-opus-5-5": {
        "in": _envf("USAGE_PRICE_CLAUDE_OPUS_5_5_IN", 28.80),
        "out": _envf("USAGE_PRICE_CLAUDE_OPUS_5_5_OUT", 144.00),
    },
    "claude-sonnet-5": {
        "in": _envf("USAGE_PRICE_CLAUDE_SONNET_5_IN", 14.40),
        "out": _envf("USAGE_PRICE_CLAUDE_SONNET_5_OUT", 72.00),
    },
    "claude-opus": {
        "in": _envf("USAGE_PRICE_CLAUDE_OPUS_IN", 36.00),
        "out": _envf("USAGE_PRICE_CLAUDE_OPUS_OUT", 180.00),
    },
    "claude-sonnet": {
        "in": _envf("USAGE_PRICE_CLAUDE_SONNET_IN", 21.60),
        "out": _envf("USAGE_PRICE_CLAUDE_SONNET_OUT", 108.00),
    },
    "claude-haiku": {
        "in": _envf("USAGE_PRICE_CLAUDE_HAIKU_IN", 7.20),
        "out": _envf("USAGE_PRICE_CLAUDE_HAIKU_OUT", 36.00),
    },
}

# Used for any Claude model not matched above (assume Opus-class pricing).
DEFAULT_ANTHROPIC_PRICE = {
    "in": _envf("USAGE_PRICE_CLAUDE_DEFAULT_IN", 36.00),
    "out": _envf("USAGE_PRICE_CLAUDE_DEFAULT_OUT", 180.00),
}

# Generic per-model pricing override: any model can be priced via the environment
# without a code change, in yuan per 1,000,000 tokens:
#   USAGE_PRICE_MODEL_DEEPSEEK_V4_FLASH_IN=1
#   USAGE_PRICE_MODEL_DEEPSEEK_V4_FLASH_OUT=2
# The model name is normalized (lowercase, non-alphanumeric -> _) and matched by
# longest prefix, so "deepseek-v4-flash-2026" resolves to the
# "DEEPSEEK_V4_FLASH" entry. These win over both built-in tables above.

# OpenAI Whisper transcription, yuan per minute of audio.
WHISPER_PER_MIN = _envf("USAGE_WHISPER_PER_MIN", 0.0432)

# gpt-image-1 generation, yuan per image (estimate for a large, high-quality image).
IMAGE_PER_CALL = _envf("USAGE_PRICE_IMAGE", 1.368)

# External web-search API calls, yuan per call (estimates; the content agent uses these).
SEARCH_PER_CALL = {
    "tavily": _envf("USAGE_PRICE_SEARCH_TAVILY", 0.0576),
    "brave": _envf("USAGE_PRICE_SEARCH_BRAVE", 0.036),
}


def _match_price(model: str, table: dict, default: dict) -> dict:
    """Return the {in, out} per-1M-token price for ``model`` (longest-prefix match)."""
    name = (model or "").lower()
    best = None
    for key in table:
        if name.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return table[best] if best else default


def _price_for(model: str) -> dict:
    """Return the {in, out} per-1M-token price for an OpenAI ``model``."""
    return _match_price(model, PRICING, DEFAULT_PRICE)


def _price_for_anthropic(model: str) -> dict:
    """Return the {in, out} per-1M-token price for a Claude ``model``."""
    return _match_price(model, PRICING_ANTHROPIC, DEFAULT_ANTHROPIC_PRICE)


_ENV_MODEL_PRICE_RE = re.compile(r"^USAGE_PRICE_MODEL_(.+?)_(IN|OUT)$")


def _env_model_pricing() -> dict:
    """Collect ``USAGE_PRICE_MODEL_<X>_IN/_OUT`` pairs from the environment.

    Returns ``{normalized_prefix: {"in": float, "out": float}}``; entries missing
    one half of the pair are ignored. Re-scanned on every call (cheap, and tests
    can set ``os.environ`` freely).
    """
    raw: dict = {}
    for key, value in os.environ.items():
        m = _ENV_MODEL_PRICE_RE.match(key)
        if not m:
            continue
        name, side = m.group(1), m.group(2).lower()
        try:
            raw.setdefault(name, {})[side] = float(value)
        except (TypeError, ValueError):
            logger.warning("Invalid float for %s=%r; ignoring", key, value)
    return {
        name.lower(): {"in": sides["in"], "out": sides["out"]}
        for name, sides in raw.items()
        if "in" in sides and "out" in sides
    }


def _env_price_for(model: str) -> Optional[dict]:
    """Return the env-configured price for ``model`` (longest prefix) or None."""
    if not model:
        return None
    name = re.sub(r"[^0-9a-z]+", "_", model.lower()).strip("_")
    best = None
    for prefix, price in _env_model_pricing().items():
        if name.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
            best_price = price
    return best_price if best else None


# ── Proactive vs reactive resolution ───────────────────────────────────────
# An explicit override (set by the background episode worker) wins; otherwise we
# infer from the Flask request context: inside a request => reactive, else proactive.

_MODE_OVERRIDE: contextvars.ContextVar = contextvars.ContextVar("usage_mode", default=None)

# Background workers set this so the usage they generate is attributed to the
# user who created the originating task (a brief's owner, the episode's enqueuer).
_USER_OVERRIDE: contextvars.ContextVar = contextvars.ContextVar("usage_user", default=None)


class user_context:
    """Context manager attributing recorded usage to ``(user_id, username)``.

    Thread-scoped like ``usage_context``: set it inside the worker thread that
    runs the task. The Flask request context still wins, so reactive rows are
    unaffected.
    """

    def __init__(self, user_id, username=None):
        self.user = (user_id, username) if user_id is not None else None
        self._token = None

    def __enter__(self):
        self._token = _USER_OVERRIDE.set(self.user)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _USER_OVERRIDE.reset(self._token)
        return False


class usage_context:
    """Context manager forcing the recorded mode (e.g. ``"proactive"``) for a block.

    ContextVars are per-thread, so wrapping the background worker thread tags every
    generation it performs as proactive without affecting request threads.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self._token = None

    def __enter__(self):
        self._token = _MODE_OVERRIDE.set(self.mode)
        return self

    def __exit__(self, *exc):
        if self._token is not None:
            _MODE_OVERRIDE.reset(self._token)
        return False


def _resolve_mode() -> str:
    override = _MODE_OVERRIDE.get()
    if override:
        return override
    try:
        from flask import has_request_context

        return "reactive" if has_request_context() else "proactive"
    except Exception:
        return "proactive"


def _resolve_user():
    """Best-effort ``(user_id, username)``: the request's user, else the worker's
    ``user_context`` override, else ``(None, None)``."""
    try:
        from flask import has_request_context

        if has_request_context():
            import insights_web  # late import: avoids an import cycle at module load

            user = insights_web.current_user()
            if user:
                return user["id"], user["username"]
    except Exception:
        pass
    override = _USER_OVERRIDE.get()
    if override:
        return override
    return None, None


# ── Recorders ──────────────────────────────────────────────────────────────

def _cost_mult() -> float:
    """Admin-set multiplier from /settings; real price × this is what gets stored."""
    try:
        import appconfig
        return max(0.0, float(appconfig.get("USAGE_COST_MULTIPLIER") or 1.0))
    except Exception:
        return 1.0


def _record(
    *,
    category: str,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
    cost_cny: float = 0.0,
    details=None,
) -> None:
    try:
        import database

        pt = int(prompt_tokens or 0)
        ct = int(completion_tokens or 0)
        user_id, username = _resolve_user()
        database.log_usage(
            mode=_resolve_mode(),
            category=category,
            provider=provider,
            model=model or "",
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
            audio_seconds=float(audio_seconds or 0.0),
            images=int(images or 0),
            cost_cny=round(float(cost_cny or 0.0) * _cost_mult(), 6),
            user_id=user_id,
            username=username,
            details=details,
        )
    except Exception:
        logger.warning("usage metering failed (non-fatal)", exc_info=True)


def record_chat(response, *, category: str, provider: str, model: str) -> None:
    """Record a ``chat.completions`` call from its response's ``usage`` block."""
    try:
        usage = getattr(response, "usage", None)
        # OpenAI exposes prompt_tokens/completion_tokens; Anthropic uses
        # input_tokens/output_tokens. Fall back so either shape is captured.
        pt = (getattr(usage, "prompt_tokens", None)
              or getattr(usage, "input_tokens", None) or 0)
        ct = (getattr(usage, "completion_tokens", None)
              or getattr(usage, "output_tokens", None) or 0)
        if provider in ("openai", "anthropic"):
            price = _env_price_for(model)
            if price is None:
                price = (_price_for(model) if provider == "openai"
                         else _price_for_anthropic(model))
            cost = pt / 1_000_000 * price["in"] + ct / 1_000_000 * price["out"]
        else:
            cost = 0.0
        _record(
            category=category,
            provider=provider,
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_cny=cost,
        )
    except Exception:
        logger.warning("record_chat failed (non-fatal)", exc_info=True)


def _audio_duration_seconds(audio_path) -> float:
    """Return the audio file's duration in seconds via ffprobe, or 0 on failure."""
    if not audio_path:
        return 0.0
    try:
        import subprocess

        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def record_transcription(
    *,
    audio_path=None,
    transcript=None,
    provider: str = "openai",
    model: str = "whisper-1",
    category: str = "transcription",
) -> None:
    """Record an audio transcription. ``provider="openai"`` bills per minute; local is $0."""
    try:
        seconds = _audio_duration_seconds(audio_path)
        if seconds <= 0 and transcript:
            # Rough fallback when ffprobe is unavailable: ~150 spoken words/minute.
            seconds = len(transcript.split()) / 150.0 * 60.0
        cost = (seconds / 60.0) * WHISPER_PER_MIN if provider == "openai" else 0.0
        _record(
            category=category,
            provider=provider,
            model=model,
            audio_seconds=seconds,
            cost_cny=cost,
        )
    except Exception:
        logger.warning("record_transcription failed (non-fatal)", exc_info=True)


def record_search(
    provider: str,
    *,
    calls: int = 1,
    category: str = "research_search",
    model: str = "",
) -> None:
    """Record external web-search API calls at a flat per-call estimate.

    Only paid SERP providers (Tavily, Brave) are billed here; OpenAI web-search
    cost is captured through ``record_chat`` on its underlying model call.
    """
    try:
        per = SEARCH_PER_CALL.get((provider or "").lower(), 0.0)
        _record(
            category=category,
            provider=provider or "search",
            model=model or (provider or "search"),
            cost_cny=per * max(0, int(calls)),
        )
    except Exception:
        logger.warning("record_search failed (non-fatal)", exc_info=True)


def record_image(
    response=None,
    *,
    model: str = "gpt-image-1",
    images: int = 1,
    provider: str = "openai",
    category: str = "thumbnail",
) -> None:
    """Record an image-generation call (per-image estimate; captures usage tokens if present)."""
    try:
        cost = IMAGE_PER_CALL * images if provider == "openai" else 0.0
        pt = ct = 0
        usage = getattr(response, "usage", None) if response is not None else None
        if usage is not None:
            pt = getattr(usage, "input_tokens", 0) or 0
            ct = getattr(usage, "output_tokens", 0) or 0
        _record(
            category=category,
            provider=provider,
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            images=images,
            cost_cny=cost,
        )
    except Exception:
        logger.warning("record_image failed (non-fatal)", exc_info=True)
