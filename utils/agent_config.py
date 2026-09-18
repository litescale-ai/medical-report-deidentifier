"""Structured generation and legacy media-agent configuration.

Supports two backends:
  - "gemini"  → Gemini API via LocalAgentConfig (requires API key)
  - "ollama"  → Native structured JSON for text; SDK for media transcription
"""

import asyncio
import math
import logging
import os
from time import perf_counter

import httpx
from pydantic import BaseModel, ValidationError

from google.antigravity import Agent, LocalAgentConfig, LocalOpenAIAgentConfig
from google.antigravity.types import BuiltinTools, CapabilitiesConfig, ModelOutputRetryConfig, RetryConfig

# Defaults for the Ollama backend
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"


def model_timeout_seconds() -> float:
    """Read one finite, positive deadline for both native and media requests."""
    try:
        timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "120"))
    except ValueError:
        raise ValueError("MODEL_TIMEOUT_SECONDS must be a finite positive number") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("MODEL_TIMEOUT_SECONDS must be a finite positive number")
    return timeout


def build_agent_config(
    *,
    system_instructions: str,
    response_schema: dict | type[BaseModel] | None = None,
    backend: str | None = None,
    api_key: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
    ollama_base_url: str | None = None,
) -> LocalAgentConfig | LocalOpenAIAgentConfig:
    """Return an agent config for the requested backend.

    Args:
        system_instructions: The system prompt for the agent.
        response_schema: Optional Pydantic model or dict for structured output.
        backend: "gemini" or "ollama". Falls back to env var AGENT_BACKEND, then "ollama".
        api_key: Gemini API key (only used when backend is "gemini").
        ollama_model: Ollama model name, e.g. "gemma4:e4b".
        ollama_base_url: Ollama OpenAI-compat endpoint URL.
    """
    backend = (backend or os.getenv("AGENT_BACKEND", "ollama")).lower().strip()
    if backend not in {"ollama", "gemini"}:
        raise ValueError(f"Unsupported model backend: {backend}")

    shared_kwargs = dict(
        system_instructions=system_instructions,
        capabilities=CapabilitiesConfig(enabled_tools=[BuiltinTools.FINISH], enable_subagents=False),
        retry_config=RetryConfig(model_output_retry=ModelOutputRetryConfig(max_retries=1)),
    )
    if response_schema is not None:
        shared_kwargs["response_schema"] = response_schema

    if backend == "ollama":
        return LocalOpenAIAgentConfig(
            model=ollama_model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=ollama_base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            **shared_kwargs,
        )

    # Explicitly selected Gemini API
    # 503 errors often mean the default model (e.g. gemini-exp) is overloaded.
    # Set explicitly via GEMINI_MODEL or default to a stable model.
    return LocalAgentConfig(
        model=gemini_model or os.getenv("GEMINI_MODEL", "gemini-3.7-flash"),
        api_key=api_key,
        **shared_kwargs,
    )


async def generate_structured(
    prompt: str,
    *,
    system_instructions: str,
    response_schema: type[BaseModel],
    backend: str | None = None,
    api_key: str | None = None,
    gemini_model: str | None = None,
    ollama_model: str | None = None,
    ollama_base_url: str | None = None,
    metrics_callback=None,
) -> dict:
    """Generate and validate one structured response, without a local tool-call loop.

    Ollama requests have a total deadline and no automatic retries. Failed or
    truncated responses must never become an empty, apparently successful report.
    Logs contain timings and token counts, not prompts or model output.
    """
    backend = (backend or os.getenv("AGENT_BACKEND", "ollama")).lower().strip()
    timeout = model_timeout_seconds()
    started = perf_counter()
    if backend != "ollama":
        config = build_agent_config(
            system_instructions=system_instructions, response_schema=response_schema,
            backend=backend, api_key=api_key, gemini_model=gemini_model,
        )
        async with asyncio.timeout(timeout):
            async with Agent(config=config) as agent:
                response = await agent.chat(prompt)
                return response_schema.model_validate(await response.structured_output()).model_dump()

    base_url = (ollama_base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)).rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    payload = {
        "model": ollama_model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": prompt},
        ],
        "format": response_schema.model_json_schema(),
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.post(f"{base_url}/api/chat", json=payload)
                response.raise_for_status()
                result = response.json()
    except (TimeoutError, httpx.TimeoutException):
        raise TimeoutError(f"Ollama exceeded the {timeout:g}s request deadline. Try a smaller document or increase MODEL_TIMEOUT_SECONDS.") from None
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"Ollama request failed (HTTP {error.response.status_code}). Check the selected model and server.") from None
    except httpx.RequestError:
        raise RuntimeError("Cannot reach Ollama. Check the server URL and that Ollama is running.") from None
    except ValueError:
        raise ValueError("Ollama returned an invalid JSON response.") from None

    if isinstance(result, dict) and metrics_callback:
        def count(key):
            value = result.get(key)
            return value if isinstance(value, (int, float)) and value >= 0 else None
        def seconds(key):
            value = count(key)
            return value / 1_000_000_000 if value is not None else None
        output_tokens, generation = count("eval_count"), seconds("eval_duration")
        metrics_callback({
            "input_tokens": count("prompt_eval_count"), "output_tokens": output_tokens,
            "generation_seconds": generation, "prompt_seconds": seconds("prompt_eval_duration"),
            "load_seconds": seconds("load_duration"), "request_seconds": perf_counter() - started,
            "tokens_per_second": output_tokens / generation if output_tokens is not None and generation else None,
        })

    if not isinstance(result, dict) or result.get("done") is not True or result.get("done_reason") != "stop":
        raise ValueError("Ollama returned an incomplete response. No report was produced; try a smaller document.")
    try:
        data = response_schema.model_validate_json(result["message"]["content"])
    except (KeyError, TypeError, ValidationError):
        raise ValueError(f"Ollama returned invalid {response_schema.__name__} data. No report was produced.") from None
    logging.getLogger(__name__).info(
        "Ollama %s completed in %.2fs (input tokens=%s, output tokens=%s)",
        response_schema.__name__, perf_counter() - started,
        result.get("prompt_eval_count"), result.get("eval_count"),
    )
    return data.model_dump()
