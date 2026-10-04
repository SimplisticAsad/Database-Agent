"""Stage 2: architecture -> PostgreSQL DDL, validated, executed, verified."""

from app.database.introspection import verify_schema_against_architecture
from app.database.schema_checker import check_schema_statements
from app.database.sql_safety import SqlKind, SqlSafetyValidator
from app.models.schema import SchemaScript
from app.pipeline.context import AttemptRecord, PipelineContext
from app.pipeline.stage import Stage, attempt_vars, numbered_sql

_SAFETY = SqlSafetyValidator(SqlKind.SCHEMA)


class SchemaStage(Stage):
    name = "schema"
    title = "Stage 2: Schema"

    def execute(self, ctx: PipelineContext) -> None:
        obs = self.services.observer
        action = self.services.schema_manager.reset()
        obs.info(self.name, f"Target schema '{self.services.schema_manager.schema}' {action} (agent-managed)")
        script = self.services.llm.generate_model(self.name, "schema/generate_schema", {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(), "POSTGRES_VERSION": ctx.pg_version,
            "ARCHITECTURE_JSON": ctx.require_architecture().model_dump_json(indent=2),
            "TARGET_SCHEMA": self.services.schema_manager.schema,
            "DATABASE_STATE_JSON": self.state_json(),
        }, SchemaScript)
        obs.ok(self.name, "Schema generated")
        self._apply(ctx, script)

    def repair_from_evidence(self, ctx: PipelineContext, evidence: str) -> None:
        """Called by the testing stage when behaviour tests blame the schema."""
        corrected = self._correct(ctx, ctx.require_schema(), evidence, [])
        action = self.services.schema_manager.reset()
        self.services.observer.info(self.name, f"Schema {action} to apply a test-driven correction")
        self._apply(ctx, corrected)

    def _apply(self, ctx: PipelineContext, script: SchemaScript) -> None:
        loop = self.correction_loop(ctx, lambda s: numbered_sql(s.statements))
        final = loop.run(script, lambda s: self._check(ctx, s), lambda s, e, h: self._correct(ctx, s, e, h))
        obs = self.services.observer
        obs.ok(self.name, "Schema validated")
        obs.ok(self.name, "Schema executed")
        ctx.schema = final
        self.services.artifacts.save_text("schema.sql", final.to_sql())
        self.services.artifacts.save_json("database_state.json", self.services.inspector.snapshot())

    def _check(self, ctx: PipelineContext, script: SchemaScript) -> str | None:
        obs, architecture = self.services.observer, ctx.require_architecture()
        problems = _SAFETY.problems(script.statements) + check_schema_statements(script.statements, architecture)
        obs.event(self.name, "validation", ok=not problems, problems=problems)
        if problems:
            return "Static SQL validation failed:\n" + "\n".join(problems)
        result = self.services.executor.run_script(
            script.statements,
            post_check=lambda _conn: verify_schema_against_architecture(self.services.inspector.snapshot(), architecture),
        )
        obs.event(self.name, "sql_execution", success=result.success, error=result.error)
        return None if result.success else "PostgreSQL error: " + result.describe()

    def _correct(self, ctx: PipelineContext, failed: SchemaScript, error: str,
                 history: list[AttemptRecord]) -> SchemaScript:
        return self.services.llm.generate_model(self.name, "correction/schema_correction", {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(), "POSTGRES_VERSION": ctx.pg_version,
            "ARCHITECTURE_JSON": ctx.require_architecture().model_dump_json(indent=2),
            "TARGET_SCHEMA": self.services.schema_manager.schema,
            "FAILED_SQL": numbered_sql(failed.statements), "ERROR_REPORT": error,
            "DATABASE_STATE_JSON": self.state_json(), **attempt_vars(history, self.services.max_attempts),
        }, SchemaScript)
