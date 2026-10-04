"""Generic bounded generate -> check -> correct loop shared by the SQL/architecture stages."""

import json
from collections.abc import Callable
from typing import Generic, TypeVar

from app.errors import StageFailedError
from app.observability import Observer
from app.pipeline.context import AttemptRecord

T = TypeVar("T")


class CorrectionLoop(Generic[T]):
    """Attempt 1 checks the artifact; every failure triggers an LLM correction, up to max_attempts.

    `check` returns None on success or an error description.
    `correct` receives (failed artifact, error, previous failed attempts) and returns a new artifact.
    """

    def __init__(self, stage: str, title: str, max_attempts: int, observer: Observer,
                 attempts: list[AttemptRecord], render: Callable[[T], str]) -> None:
        self._stage, self._title = stage, title
        self._max = max_attempts
        self._observer = observer
        self._attempts = attempts
        self._render = render

    def run(self, artifact: T, check: Callable[[T], str | None],
            correct: Callable[[T, str, list[AttemptRecord]], T]) -> T:
        previous: list[AttemptRecord] = []
        corrected = False
        for attempt in range(1, self._max + 1):
            error = check(artifact)
            record = AttemptRecord(self._stage, attempt, "failed" if error else "success", error, self._render(artifact))
            self._attempts.append(record)
            self._observer.event(self._stage, "attempt", attempt=attempt,
                                 max_attempts=self._max, status=record.status, error=error)
            if error is None:
                if corrected:
                    self._observer.ok(self._stage, "Correction successful.")
                return artifact
            self._report_failure(attempt, error)
            previous.append(record)
            if len(previous) == self._max:
                raise StageFailedError(self._stage, f"still failing after {self._max} attempts. Last error: {error}")
            self._observer.console.info("LLM correction requested...")
            artifact = correct(artifact, error, previous[:-1])
            corrected = True
        raise AssertionError("unreachable")

    def _report_failure(self, number: int, error: str) -> None:
        self._observer.console.fail(f"{self._title}  |  Attempt: {number} / {self._max}")
        for line in error.splitlines()[:12]:
            self._observer.console.info(line)


def history_json(previous: list[AttemptRecord]) -> str:
    """Earlier failed attempts (excluding the one currently being corrected)."""
    return json.dumps(
        [{"attempt": r.attempt, "error": r.error, "artifact": r.artifact} for r in previous], indent=2
    )
