"""Centralised agent configuration factory.

Supports two backends:
  - "gemini"  → Gemini API via LocalAgentConfig (requires API key)
  - "ollama"  → Local Gemma model via LocalOpenAIAgentConfig (requires Ollama running)
"""

import os
from pydantic import BaseModel

from google.antigravity import LocalAgentConfig, LocalOpenAIAgentConfig

# Defaults for the Ollama backend
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"


def build_agent_config(
    *,
    system_instructions: str,
    response_schema: dict | type[BaseModel] | None = None,
    backend: str | None = None,
    api_key: str | None = None,
    ollama_model: str | None = None,
    ollama_base_url: str | None = None,
) -> LocalAgentConfig | LocalOpenAIAgentConfig:
    """Return an agent config for the requested backend.

    Args:
        system_instructions: The system prompt for the agent.
        response_schema: Optional Pydantic model or dict for structured output.
        backend: "gemini" or "ollama". Falls back to env var AGENT_BACKEND, then "gemini".
        api_key: Gemini API key (only used when backend is "gemini").
        ollama_model: Ollama model name, e.g. "gemma4:e4b".
        ollama_base_url: Ollama OpenAI-compat endpoint URL.
    """
    backend = (backend or os.getenv("AGENT_BACKEND", "gemini")).lower().strip()

    shared_kwargs = dict(
        system_instructions=system_instructions,
    )
    if response_schema is not None:
        shared_kwargs["response_schema"] = response_schema

    if backend == "ollama":
        return LocalOpenAIAgentConfig(
            model=ollama_model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=ollama_base_url or os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            **shared_kwargs,
        )

    # Default: Gemini API
    return LocalAgentConfig(
        api_key=api_key,
        **shared_kwargs,
    )
