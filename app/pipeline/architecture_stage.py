"""Stage 1: entity JSON -> validated database architecture."""

import json

from app.database.dependency_graph import DependencyGraph
from app.database.validator import ArchitectureValidator
from app.models.architecture import Architecture
from app.pipeline.context import AttemptRecord, PipelineContext
from app.pipeline.stage import Stage, StageServices, attempt_vars


class ArchitectureStage(Stage):
    name = "architecture"
    title = "Stage 1: Architecture"

    def __init__(self, services: StageServices, validator: ArchitectureValidator | None = None) -> None:
        super().__init__(services)
        self._validator = validator or ArchitectureValidator()

    def execute(self, ctx: PipelineContext) -> None:
        obs = self.services.observer
        proposal = self.services.llm.generate_model(self.name, "architecture/generate_architecture", {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(), "POSTGRES_VERSION": ctx.pg_version,
        }, Architecture)
        obs.ok(self.name, "Architecture generated")

        loop = self.correction_loop(ctx, lambda a: a.model_dump_json(indent=2))
        validated = loop.run(proposal, lambda a: self._check(ctx, a), lambda a, e, h: self._correct(ctx, a, e, h))
        ctx.architecture = self._finalize(validated, ctx)
        obs.ok(self.name, "Architecture validated")
        self.services.artifacts.save_json("architecture.json", ctx.architecture)
        obs.ok(self.name, "architecture.json saved")

    def _check(self, ctx: PipelineContext, architecture: Architecture) -> str | None:
        report = self._validator.validate(architecture, ctx.requirements)
        self.services.observer.event(self.name, "validation", ok=report.ok, report=report.format())
        return None if report.ok else report.format()

    def _correct(self, ctx: PipelineContext, failed: Architecture, error: str,
                 history: list[AttemptRecord]) -> Architecture:
        return self.services.llm.generate_model(self.name, "correction/architecture_correction", {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(),
            "POSTGRES_VERSION": ctx.pg_version,
            "FAILED_ARCHITECTURE_JSON": failed.model_dump_json(indent=2),
            "VALIDATION_ERRORS": error, **attempt_vars(history, self.services.max_attempts),
        }, Architecture)

    def _finalize(self, architecture: Architecture, ctx: PipelineContext) -> Architecture:
        """Deterministic fields: the system, not the LLM, owns ordering and dependencies."""
        graph = DependencyGraph.from_architecture(architecture)
        order = graph.topological_order()
        if order != architecture.creation_order:
            self.services.observer.event(self.name, "creation_order_recomputed",
                                         llm_order=architecture.creation_order, order=order)
        tables = [t.model_copy(update={"dependencies": sorted(graph.edges[t.name])}) for t in architecture.tables]
        return architecture.model_copy(update={"tables": tables, "creation_order": order})
