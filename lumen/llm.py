"""Provider-agnostic LLM config. Any OpenAI-compatible endpoint works.

Two separate clients so free-tier rate limits are independent pools:
  agent     -> LLM_*  (Groq by default, fast)
  simulator -> SIM_*  (Gemini by default; plays the borrower and judges). A different model
               family from the agent, so the agent is not grading its own homework.
"""
from __future__ import annotations

import asyncio
import os
import re

from openai import AsyncOpenAI, RateLimitError

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
AGENT_MODEL = os.getenv("AGENT_MODEL", "openai/gpt-oss-20b")
# On a 429 we fail over to the next model immediately (each model has its own free-tier limit).
AGENT_FALLBACKS = [m for m in os.getenv("AGENT_FALLBACKS", "qwen/qwen3.8-27b,openai/gpt-oss-120b").split(",") if m]
SIM_MODEL = os.getenv("SIM_MODEL", "gemini-3-flash-preview")
SIM_BASE_URL = os.getenv("SIM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")


def make_client() -> AsyncOpenAI:
    """Agent client. Retries are handled by `with_backoff` so waiting time is measurable."""
    return AsyncOpenAI(base_url=BASE_URL, api_key=os.getenv("LLM_API_KEY") or "missing", max_retries=0)


def make_sim_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=SIM_BASE_URL, api_key=os.getenv("SIM_API_KEY") or "missing", max_retries=0)


class RateLimitClock:
    """Accumulates time spent waiting on 429s, so latency can be reported with and without it."""
    waited_s: float = 0.0


async def with_backoff(fn, *, tries: int = 6, clock: type[RateLimitClock] = RateLimitClock):
    delay = 2.0
    for attempt in range(tries):
        try:
            return await fn()
        except RateLimitError as e:
            if attempt == tries - 1:
                raise
            # honour the provider's hint when present ("try again in 7.98s")
            m = re.search(r"try again in ([\d.]+)s", str(e))
            wait = float(m.group(1)) + 0.5 if m else delay
            clock.waited_s += wait
            await asyncio.sleep(wait)
            delay = min(delay * 2, 20)


def to_openai_tools(schemas: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                              "parameters": s["input_schema"]}} for s in schemas]


async def chat_with_failover(client, *, messages, tools, exclude=frozenset(),
                             clock: type[RateLimitClock] = RateLimitClock, **kw):
    """Stream a chat completion. On 429, try the next model at once; only sleep if all are limited.
    `exclude` skips models that already returned a malformed tool call this turn.
    Returns (stream, model_used)."""
    models = [m for m in [AGENT_MODEL, *AGENT_FALLBACKS] if m not in exclude] or [AGENT_MODEL, *AGENT_FALLBACKS]
    for rnd in range(14):
        for model in models:
            extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
            try:
                stream = await client.chat.completions.create(
                    model=model, messages=messages, tools=tools, stream=True, extra_body=extra, **kw)
                return stream, model
            except RateLimitError as e:
                last = e
        m = re.search(r"try again in ([\d.]+)s", str(last))
        wait = min(float(m.group(1)) + 0.5 if m else 3.0, 15.0)
        clock.waited_s += wait
        await asyncio.sleep(wait)
    raise last
