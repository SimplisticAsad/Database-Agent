"""Runs the stages in order against one PipelineContext and reports the outcome."""

from dataclasses import dataclass

from app.artifacts import ArtifactStore
from app.errors import DatabaseAgentError, StageFailedError
from app.observability import Observer
from app.pipeline.context import PipelineContext
from app.pipeline.stage import Stage


@dataclass
class PipelineResult:
    context: PipelineContext
    failed_stage: str | None = None
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.failed_stage is None

    @property
    def tests_passed(self) -> bool:
        report = self.context.test_report
        return self.completed and report is not None and report.failed == 0


class PipelineOrchestrator:
    def __init__(self, stages: list[Stage], observer: Observer, artifacts: ArtifactStore) -> None:
        self._stages = stages
        self._observer = observer
        self._artifacts = artifacts

    def run(self, ctx: PipelineContext) -> PipelineResult:
        result = PipelineResult(ctx)
        for stage in self._stages:
            try:
                stage.run(ctx)
            except DatabaseAgentError as exc:
                stage_name = exc.stage if isinstance(exc, StageFailedError) else stage.name
                ctx.errors.append(str(exc))
                self._observer.fail(stage_name, f"{stage.title} failed: {exc}", final_status="failed")
                result = PipelineResult(ctx, stage_name, str(exc))
                break
        self._artifacts.save_json("attempts.json", [a.as_dict() for a in ctx.attempts])
        self._observer.event("pipeline", "finished", success=result.tests_passed, failed_stage=result.failed_stage)
        return result
