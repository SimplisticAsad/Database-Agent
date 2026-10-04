"""Concrete provider(s) and the factory that selects one from configuration."""

import os

import anthropic

from app.config.settings import Settings
from app.errors import LLMError
from app.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic SDK. Credentials come from settings/environment."""

    def __init__(self, model: str, max_tokens: int, timeout: float, api_key: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("The model refused this request")
        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"Response truncated at {self._max_tokens} tokens; increase LLM_MAX_TOKENS"
            )
        return "".join(block.text for block in response.content if block.type == "text")


def create_provider(settings: Settings) -> LLMProvider:
    """Add new providers here (OpenAI, local, ...) without touching the pipeline."""
    name = settings.llm_provider.lower()
    if name == "anthropic":
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        if not key and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError("ANTHROPIC_API_KEY is not set (see .env.example)")
        return AnthropicProvider(settings.llm_model, settings.llm_max_tokens, settings.llm_timeout_seconds, key)
    raise LLMError(f"Unknown LLM_PROVIDER '{settings.llm_provider}' (supported: anthropic)")
