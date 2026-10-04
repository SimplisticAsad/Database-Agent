"""Stage 3: schema -> PostgreSQL CRUD functions/procedures, executed and verified."""

from app.database.sql_safety import SqlKind, SqlSafetyValidator
from app.models.crud import CrudScript
from app.pipeline.context import AttemptRecord, PipelineContext
from app.pipeline.stage import Stage, attempt_vars, numbered_sql

_SAFETY = SqlSafetyValidator(SqlKind.CRUD)


class CrudStage(Stage):
    name = "crud"
    title = "Stage 3: CRUD"

    def execute(self, ctx: PipelineContext) -> None:
        script = self.services.llm.generate_model(self.name, "crud/generate_crud", {
            **self._context_vars(ctx), "DATABASE_STATE_JSON": self.state_json(),
        }, CrudScript)
        self.services.observer.ok(self.name, "CRUD generated")
        self._apply(ctx, script)

    def reapply(self, ctx: PipelineContext) -> None:
        """Re-run the current CRUD script (e.g. after the schema was rebuilt)."""
        self._apply(ctx, ctx.require_crud())

    def repair_from_evidence(self, ctx: PipelineContext, evidence: str) -> None:
        """Called by the testing stage when behaviour tests blame the CRUD functions."""
        self._apply(ctx, self._correct(ctx, ctx.require_crud(), evidence, []))

    def _apply(self, ctx: PipelineContext, script: CrudScript) -> None:
        loop = self.correction_loop(ctx, lambda s: numbered_sql(s.statements))
        final = loop.run(script, lambda s: self._check(s), lambda s, e, h: self._correct(ctx, s, e, h))
        self.services.observer.ok(self.name, "CRUD executed")
        ctx.crud = final
        self.services.artifacts.save_text("crud.sql", final.to_sql())
        self.services.artifacts.save_json("database_state.json", self.services.inspector.snapshot())

    def _check(self, script: CrudScript) -> str | None:
        obs = self.services.observer
        problems = _SAFETY.problems(script.statements)
        obs.event(self.name, "validation", ok=not problems, problems=problems)
        if problems:
            return "Static SQL validation failed:\n" + "\n".join(problems)
        declared = {f.name.lower() for f in script.functions}
        result = self.services.executor.run_script(
            script.statements, post_check=lambda _c: self._missing_functions(declared)
        )
        obs.event(self.name, "sql_execution", success=result.success, error=result.error)
        return None if result.success else "PostgreSQL error: " + result.describe()

    def _missing_functions(self, declared: set[str]) -> list[str]:
        actual = {f["name"].lower() for f in self.services.inspector.snapshot()["functions"]}
        return [f"declared function '{name}' does not exist after execution" for name in sorted(declared - actual)]

    def _context_vars(self, ctx: PipelineContext) -> dict[str, str]:
        return {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(), "POSTGRES_VERSION": ctx.pg_version,
            "ARCHITECTURE_JSON": ctx.require_architecture().model_dump_json(indent=2),
            "SCHEMA_SQL": ctx.require_schema().to_sql(), "TARGET_SCHEMA": self.services.schema_manager.schema,
        }

    def _correct(self, ctx: PipelineContext, failed: CrudScript, error: str,
                 history: list[AttemptRecord]) -> CrudScript:
        return self.services.llm.generate_model(self.name, "correction/crud_correction", {
            **self._context_vars(ctx), "FAILED_SQL": numbered_sql(failed.statements), "ERROR_REPORT": error,
            "DATABASE_STATE_JSON": self.state_json(), **attempt_vars(history, self.services.max_attempts),
        }, CrudScript)
