"""Stage base class (Template Method) and the shared services stages receive via DI."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.artifacts import ArtifactStore
from app.database.executor import DatabaseExecutor
from app.database.introspection import DatabaseInspector
from app.database.schema_manager import SchemaManager
from app.llm.client import LLMClient
from app.observability import Observer
from app.pipeline.context import AttemptRecord, PipelineContext
from app.pipeline.correction import CorrectionLoop, history_json


@dataclass
class StageServices:
    llm: LLMClient
    observer: Observer
    artifacts: ArtifactStore
    executor: DatabaseExecutor
    inspector: DatabaseInspector
    schema_manager: SchemaManager
    max_attempts: int
    max_test_repair_rounds: int


class Stage(ABC):
    """run() = announce -> execute -> (stage persists its own artifacts)."""

    name: str
    title: str

    def __init__(self, services: StageServices) -> None:
        self.services = services

    def run(self, ctx: PipelineContext) -> None:
        self.services.observer.heading(self.name, self.title)
        self.execute(ctx)
        self.services.observer.event(self.name, "stage_completed")

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> None:
        ...

    def state_json(self) -> str:
        """Live catalog snapshot (tables, constraints, indexes, functions) for prompts."""
        return json.dumps(self.services.inspector.snapshot(), indent=2, default=str)

    def correction_loop(self, ctx: PipelineContext, render) -> CorrectionLoop:
        return CorrectionLoop(self.name, self.title, self.services.max_attempts,
                              self.services.observer, ctx.attempts, render)


def numbered_sql(statements: list[str]) -> str:
    return "\n\n".join(f"-- statement {i}\n{s}" for i, s in enumerate(statements, 1))


def attempt_vars(attempt_history: list[AttemptRecord], max_attempts: int) -> dict[str, str]:
    return {
        "PREVIOUS_ATTEMPTS_JSON": history_json(attempt_history),
        "ATTEMPT_NUMBER": str(len(attempt_history) + 2),
        "MAX_ATTEMPTS": str(max_attempts),
    }
