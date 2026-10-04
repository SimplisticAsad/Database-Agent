"""Provider-neutral LLM interface (Strategy pattern)."""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Turns a fully rendered prompt into raw text. Nothing else."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        ...
