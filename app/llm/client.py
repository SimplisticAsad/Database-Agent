"""Prompt-file + provider + Pydantic validation, with a bounded JSON-repair loop."""

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.errors import StructuredOutputError
from app.llm.base import LLMProvider
from app.llm.prompt_loader import PromptLoader
from app.observability import Observer

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
STRUCTURED_CORRECTION_PROMPT = "correction/structured_output_correction"


def extract_json(raw: str) -> object:
    """Parse JSON from LLM text, tolerating code fences and surrounding prose."""
    text = _FENCE.sub("", raw.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def parse_model(raw: str, model: type[T]) -> T:
    """Raises ValueError (JSON error) or ValidationError with a readable message."""
    return model.model_validate(extract_json(raw))


class LLMClient:
    def __init__(self, provider: LLMProvider, prompts: PromptLoader, observer: Observer, max_attempts: int = 3) -> None:
        self._provider = provider
        self._prompts = prompts
        self._observer = observer
        self._max_attempts = max_attempts

    def generate_model(self, stage: str, prompt_id: str, variables: dict[str, str], model: type[T]) -> T:
        """Render a prompt file, call the LLM, validate into `model`; repair malformed output."""
        schema = json.dumps(model.model_json_schema(), indent=2)
        prompt = self._prompts.render(prompt_id, {**variables, "OUTPUT_JSON_SCHEMA": schema})
        raw = self._call(stage, prompt_id, prompt)
        for attempt in range(1, self._max_attempts + 1):
            try:
                return parse_model(raw, model)
            except (ValueError, ValidationError) as exc:
                self._observer.event(stage, "structured_output_invalid", prompt_id=prompt_id,
                                     attempt=attempt, error=str(exc))
                if attempt == self._max_attempts:
                    raise StructuredOutputError(
                        f"{prompt_id}: no valid {model.__name__} after {attempt} attempts: {exc}"
                    ) from exc
                self._observer.info(stage, f"LLM output invalid ({_first_line(exc)}); requesting repair "
                                           f"{attempt}/{self._max_attempts - 1}")
                repair = self._prompts.render(STRUCTURED_CORRECTION_PROMPT, {
                    "RAW_OUTPUT": raw, "PARSE_ERROR": str(exc), "OUTPUT_JSON_SCHEMA": schema,
                    "ATTEMPT_NUMBER": str(attempt), "MAX_ATTEMPTS": str(self._max_attempts - 1),
                })
                raw = self._call(stage, STRUCTURED_CORRECTION_PROMPT, repair)
        raise AssertionError("unreachable")

    def _call(self, stage: str, prompt_id: str, prompt: str) -> str:
        self._observer.event(stage, "llm_request", prompt_id=prompt_id, prompt=prompt)
        response = self._provider.generate(prompt)
        self._observer.event(stage, "llm_response", prompt_id=prompt_id, response=response)
        return response


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:120]
