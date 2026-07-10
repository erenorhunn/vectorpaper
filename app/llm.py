"""LLM access — OpenAI-compatible clients for Ollama (default), Claude, and Gemini.
The provider is a per-project setting. Token-budget guard + backoff (doc Risk 2),
every call logged to Postgres (doc §6 observability).
"""

import json
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from openai import AsyncOpenAI
from sqlalchemy import func, select

from .config import settings
from .db import Session
from .models import LlmCall, Paper, Project

# Anthropic and Google both ship OpenAI-compatible chat endpoints → one client type for all.
PROVIDERS = {
    "ollama": {"base_url": settings.llm_base_url, "api_key": settings.llm_api_key,
               "summary_model": settings.summary_model, "reasoning_model": settings.reasoning_model},
    "claude": {"base_url": "https://api.anthropic.com/v1/", "api_key": settings.anthropic_api_key,
               "summary_model": settings.claude_model, "reasoning_model": settings.claude_model},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "api_key": settings.gemini_api_key,
               "summary_model": settings.gemini_model, "reasoning_model": settings.gemini_model},
}

_clients: dict[str, AsyncOpenAI] = {}


def _client(provider: str) -> AsyncOpenAI:
    cfg = PROVIDERS.get(provider) or PROVIDERS["ollama"]
    if not cfg["api_key"]:
        raise RuntimeError(f"{provider}: API key not configured (see .env.example)")
    if provider not in _clients:
        _clients[provider] = AsyncOpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], max_retries=3)
    return _clients[provider]


async def provider_for_paper(paper_id: str) -> str:
    """Resolve the LLM provider from the paper's project settings."""
    async with Session() as s:
        paper = await s.get(Paper, paper_id)
        project = await s.get(Project, paper.project_id) if paper and paper.project_id else None
    return (project.settings or {}).get("provider", "ollama") if project else "ollama"


class BudgetExceeded(Exception):
    pass


async def _check_budget() -> None:
    today = datetime.now(timezone.utc).date()
    async with Session() as s:
        used = await s.scalar(
            select(func.coalesce(func.sum(LlmCall.prompt_tokens + LlmCall.completion_tokens), 0))
            .where(func.date(LlmCall.created_at) == today)
        )
    if used >= settings.daily_token_budget:
        raise BudgetExceeded(f"daily token budget {settings.daily_token_budget} exhausted ({used} used)")


async def _log(model: str, purpose: str, pt: int, ct: int, ms: int) -> None:
    async with Session() as s:
        s.add(LlmCall(model=model, purpose=purpose, prompt_tokens=pt, completion_tokens=ct, latency_ms=ms))
        await s.commit()


async def complete(purpose: str, system: str, user: str, model: str | None = None,
                   provider: str = "ollama", temperature: float = 0.1) -> str:
    await _check_budget()
    model = model or PROVIDERS.get(provider, PROVIDERS["ollama"])["summary_model"]
    t0 = time.monotonic()
    r = await _client(provider).chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    usage = r.usage
    await _log(model, purpose, usage.prompt_tokens if usage else 0,
               usage.completion_tokens if usage else 0, int((time.monotonic() - t0) * 1000))
    text = r.choices[0].message.content or ""
    # qwen3-style thinking models may emit <think>...</think> — strip it
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


def extract_json(text: str) -> str | None:
    """Tolerant JSON lift: prefer a ```json fenced block, else the first balanced {...}/[...]."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    close = {"{": "}", "[": "]"}[text[start]]
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == text[start]:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


async def complete_json(purpose: str, system: str, user: str, provider: str = "ollama",
                        temperature: float = 0.1) -> dict | list:
    """complete() + tolerant JSON parse; one retry with a stricter system prompt."""
    for _ in range(2):
        text = await complete(purpose, system, user, provider=provider, temperature=temperature)
        raw = extract_json(text)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        system += "\nReturn ONLY valid JSON, no prose, no markdown fences."
    raise ValueError(f"{purpose}: model did not return valid JSON")


async def stream(purpose: str, system: str, user: str, model: str | None = None,
                 provider: str = "ollama") -> AsyncIterator[str]:
    """Token stream for SSE (Adım 5). Logs approximate usage at the end."""
    await _check_budget()
    model = model or PROVIDERS.get(provider, PROVIDERS["ollama"])["reasoning_model"]
    t0 = time.monotonic()
    out_len = 0
    resp = await _client(provider).chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        stream=True,
    )
    async for chunk in resp:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            out_len += len(delta)
            yield delta
    # ponytail: streamed usage not returned by all providers — approximate from chars
    await _log(model, purpose, len(system + user) // 4, out_len // 4,
               int((time.monotonic() - t0) * 1000))
