"""Concrete provider(s) and the factory that selects one from configuration."""

import os
import re
import time
from collections.abc import Callable

import anthropic
import openai

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


class OpenAICompatibleProvider(LLMProvider):
    """Any server speaking the OpenAI chat-completions API: Gemini, Ollama, Groq, OpenRouter, ..."""

    def __init__(self, model: str, api_key: str, base_url: str, max_tokens: int, timeout: float,
                 json_mode: bool = True, min_interval: float = 0.0, rate_limit_retries: int = 6,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens
        self._json_mode = json_mode
        self._min_interval = min_interval
        self._rate_limit_retries = rate_limit_retries
        self._sleep, self._clock = sleep, clock
        self._last_call: float | None = None

    def generate(self, prompt: str) -> str:
        try:
            response = self._request_with_backoff(prompt)
        except openai.OpenAIError as exc:
            raise LLMError(f"LLM API error: {exc}") from exc
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"Response truncated at {self._max_tokens} tokens; increase LLM_MAX_TOKENS")
        if not choice.message.content:
            raise LLMError(f"Empty LLM response (finish_reason={choice.finish_reason})")
        return choice.message.content


    def _request_with_backoff(self, prompt: str):
        options = {"response_format": {"type": "json_object"}} if self._json_mode else {}
        for attempt in range(self._rate_limit_retries + 1):
            self._throttle()
            try:
                return self._client.chat.completions.create(
                    model=self._model, max_tokens=self._max_tokens,
                    messages=[{"role": "user", "content": prompt}], **options,
                )
            except _TRANSIENT as exc:
                if attempt == self._rate_limit_retries:
                    raise
                if "PerDay" in str(exc):  # daily quota: waiting seconds cannot help
                    raise LLMError(f"Daily quota exhausted for model '{self._model}'; "
                                   "switch LLM_MODEL or wait for the reset") from exc
                self._sleep(_retry_delay(str(exc), attempt))

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()


_TRANSIENT = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)
_RETRY_HINT = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _retry_delay(message: str, attempt: int) -> float:
    """Honour the server's 'retry in Ns' hint, else back off exponentially (capped)."""
    match = _RETRY_HINT.search(message)
    return (float(match.group(1)) + 1.0) if match else min(5.0 * 2**attempt, 60.0)


def create_provider(settings: Settings) -> LLMProvider:
    """Add new providers here (OpenAI, local, ...) without touching the pipeline."""
    name = settings.llm_provider.lower()
    if name == "anthropic":
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        if not key and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError("ANTHROPIC_API_KEY is not set (see .env.example)")
        return AnthropicProvider(settings.llm_model, settings.llm_max_tokens, settings.llm_timeout_seconds, key)
    if name == "openai_compatible":
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not set (see .env.example)")
        return OpenAICompatibleProvider(
            settings.llm_model, settings.llm_api_key.get_secret_value(), settings.llm_base_url,
            settings.llm_max_tokens, settings.llm_timeout_seconds, settings.llm_json_mode,
            settings.llm_min_interval_seconds, settings.llm_rate_limit_retries,
        )
    raise LLMError(f"Unknown LLM_PROVIDER '{settings.llm_provider}' (supported: anthropic, openai_compatible)")
