"""Stage 4: BDD test generation, execution, triage of failures, and targeted repair."""

import json
import re

from app.database.bdd_runner import BddRunner
from app.database.sql_safety import SqlKind, SqlSafetyValidator
from app.errors import StageFailedError
from app.models.tests import (
    BddSuite, Diagnosis, Feature, FailureSource, Scenario, TestReport, TriageResult,
)
from app.pipeline.context import PipelineContext
from app.pipeline.crud_stage import CrudStage
from app.pipeline.schema_stage import SchemaStage
from app.pipeline.stage import Stage, StageServices

_SAFETY = SqlSafetyValidator(SqlKind.TEST)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def suite_problems(suite: BddSuite) -> list[str]:
    """Static checks: SQL safety and that every {{variable}} is captured before use."""
    problems: list[str] = []
    for _, scenario in suite.scenarios():
        captured: set[str] = set()
        for step in scenario.steps:
            if step.sql:
                problems += [f"scenario '{scenario.name}': {p}" for p in _SAFETY.problems([step.sql])]
                for name in _PLACEHOLDER.findall(step.sql):
                    if name not in captured:
                        problems.append(f"scenario '{scenario.name}': variable '{name}' used before it is captured")
            captured.update(step.capture)
    return problems


def render_feature(feature: Feature) -> str:
    lines = [f"Feature: {feature.name}"]
    if feature.description:
        lines.append(f"  {feature.description}")
    for scenario in feature.scenarios:
        lines += ["", f"  Scenario: {scenario.name}  # {scenario.category.value}"]
        lines += [f"    {step.keyword.value} {step.text}" for step in scenario.steps]
    return "\n".join(lines) + "\n"


def merge_scenarios(suite: BddSuite, fixes: BddSuite) -> tuple[BddSuite, list[str]]:
    """Replace scenarios by name; returns the new suite and names that matched nothing."""
    replacements: dict[str, Scenario] = {s.name: s for _, s in fixes.scenarios()}
    used: set[str] = set()
    features = []
    for feature in suite.features:
        scenarios = []
        for scenario in feature.scenarios:
            if scenario.name in replacements:
                used.add(scenario.name)
            scenarios.append(replacements.get(scenario.name, scenario))
        features.append(feature.model_copy(update={"scenarios": scenarios}))
    return suite.model_copy(update={"features": features}), sorted(set(replacements) - used)


class TestingStage(Stage):
    __test__ = False
    name = "testing"
    title = "Stage 4: Tests"

    def __init__(self, services: StageServices, runner: BddRunner, schema_stage: SchemaStage,
                 crud_stage: CrudStage) -> None:
        super().__init__(services)
        self._runner = runner
        self._schema_stage = schema_stage
        self._crud_stage = crud_stage

    def execute(self, ctx: PipelineContext) -> None:
        obs = self.services.observer
        suite = self.services.llm.generate_model(self.name, "testing/generate_bdd_tests", {
            **self._base_vars(ctx), "DATABASE_STATE_JSON": self.state_json(),
        }, BddSuite)
        obs.ok(self.name, "Tests generated")
        ctx.tests = self._static_repair(ctx, suite)
        self.save_tests(ctx)
        report = self._run(ctx)
        for round_number in range(1, self.services.max_test_repair_rounds + 1):
            if not report.failures:
                break
            obs.info(self.name, f"{report.failed} test(s) failed; triage round {round_number}"
                                f"/{self.services.max_test_repair_rounds}")
            self._repair(ctx, report)
            report = self._run(ctx)
        ctx.test_report = report
        self._save_report(report)
        (obs.ok if not report.failures else obs.fail)(
            self.name, f"Tests executed: {report.passed}/{report.total} passed")

    # -- generation / validation ----------------------------------------------
    def _static_repair(self, ctx: PipelineContext, suite: BddSuite) -> BddSuite:
        for attempt in range(1, self.services.max_attempts + 1):
            problems = suite_problems(suite)
            self.services.observer.event(self.name, "validation", ok=not problems, problems=problems)
            if not problems:
                return suite
            self.services.observer.fail(self.name, f"Generated tests invalid (attempt {attempt}): {problems[0]}")
            if attempt == self.services.max_attempts:
                raise StageFailedError(self.name, "generated tests never passed static validation: " + "; ".join(problems))
            suite = self._apply_fixes(ctx, suite, "\n".join(problems), diagnosis_json="[]")
        raise AssertionError("unreachable")

    def _apply_fixes(self, ctx: PipelineContext, suite: BddSuite, problems: str, diagnosis_json: str) -> BddSuite:
        fixes = self.services.llm.generate_model(self.name, "correction/test_correction", {
            **self._base_vars(ctx), "CURRENT_SUITE_JSON": suite.model_dump_json(indent=2),
            "PROBLEMS": problems, "DIAGNOSES_JSON": diagnosis_json, "DATABASE_STATE_JSON": self.state_json(),
        }, BddSuite)
        merged, unmatched = merge_scenarios(suite, fixes)
        if unmatched:
            self.services.observer.event(self.name, "unmatched_fixes", scenarios=unmatched)
        return merged

    # -- execution --------------------------------------------------------------
    def _run(self, ctx: PipelineContext) -> TestReport:
        report = self._runner.run(ctx.tests)
        self.services.observer.event(self.name, "test_run", total=report.total, passed=report.passed,
                                     failed=report.failed, failures=[f.model_dump() for f in report.failures])
        return report

    def _save_report(self, report: TestReport) -> None:
        store = self.services.artifacts
        store.save_json("test-report.json", {"total": report.total, "passed": report.passed,
                                              "failed": report.failed,
                                              "results": [r.model_dump() for r in report.results]})
        store.save_text("test-report.txt", report.render())

    def save_tests(self, ctx: PipelineContext) -> None:
        store = self.services.artifacts
        store.reset_dir("tests")
        for feature in ctx.tests.features:
            slug = re.sub(r"[^a-z0-9]+", "_", feature.name.lower()).strip("_") or "feature"
            store.save_text(f"tests/{slug}.feature", render_feature(feature))
        store.save_json("tests/suite.json", ctx.tests)

    # -- failure feedback ---------------------------------------------------------
    def _repair(self, ctx: PipelineContext, report: TestReport) -> None:
        diagnoses = self._triage(ctx, report)
        for d in diagnoses:
            self.services.observer.info(self.name, f"  {d.scenario}: blamed on {d.source.value} - {d.reasoning[:100]}")
        by_source = {s: [d for d in diagnoses if d.source is s] for s in FailureSource}
        if by_source[FailureSource.TEST]:
            ctx.tests = self._fix_tests(ctx, by_source[FailureSource.TEST])
        schema_issues, crud_issues = by_source[FailureSource.SCHEMA], by_source[FailureSource.CRUD]
        if schema_issues:
            self._schema_stage.repair_from_evidence(ctx, self._evidence(report, schema_issues))
            self._crud_repair_or_reapply(ctx, report, crud_issues)
        elif crud_issues:
            self._crud_stage.repair_from_evidence(ctx, self._evidence(report, crud_issues))
        self.save_tests(ctx)

    def _crud_repair_or_reapply(self, ctx, report, crud_issues: list[Diagnosis]) -> None:
        if crud_issues:
            self._crud_stage.repair_from_evidence(ctx, self._evidence(report, crud_issues))
        else:
            self._crud_stage.reapply(ctx)

    def _triage(self, ctx: PipelineContext, report: TestReport) -> list[Diagnosis]:
        scenarios = {s.name: s for _, s in ctx.tests.scenarios()}
        failures = [{"result": r.model_dump(), "scenario": scenarios[r.scenario].model_dump(mode="json")}
                    for r in report.failures]
        result = self.services.llm.generate_model(self.name, "correction/test_failure_triage", {
            **self._base_vars(ctx), "DATABASE_STATE_JSON": self.state_json(),
            "FAILURES_JSON": json.dumps(failures, indent=2, default=str),
        }, TriageResult)
        known = {d.scenario for d in result.diagnoses}
        extra = [Diagnosis(scenario=r.scenario, source=FailureSource.TEST, reasoning="not diagnosed by the LLM")
                 for r in report.failures if r.scenario not in known]
        return [d for d in result.diagnoses if d.scenario in scenarios] + extra

    def _fix_tests(self, ctx: PipelineContext, diagnoses: list[Diagnosis]) -> BddSuite:
        suite = self._apply_fixes(ctx, ctx.tests, "Scenarios failed at runtime and were diagnosed as test defects.",
                                  json.dumps([d.model_dump(mode="json") for d in diagnoses], indent=2))
        problems = suite_problems(suite)
        return suite if not problems else ctx.tests  # keep the old suite rather than an unsafe one

    @staticmethod
    def _evidence(report: TestReport, diagnoses: list[Diagnosis]) -> str:
        results = {r.scenario: r for r in report.failures}
        blocks = []
        for d in diagnoses:
            r = results.get(d.scenario)
            blocks.append(
                f"Scenario: {d.scenario}\n  Failed step: {r.failed_step if r else '?'}\n"
                f"  Expected: {r.expected if r else '?'}\n  Actual: {r.actual if r else '?'}\n"
                f"  Diagnosis: {d.reasoning}\n  Suggested fix: {d.suggested_fix}"
            )
        return "Behavioural tests exposed defects in the existing database objects:\n\n" + "\n\n".join(blocks)

    def _base_vars(self, ctx: PipelineContext) -> dict[str, str]:
        return {
            "PROJECT_NAME": ctx.project, "ENTITY_JSON": ctx.entity_json(), "POSTGRES_VERSION": ctx.pg_version,
            "ARCHITECTURE_JSON": ctx.require_architecture().model_dump_json(indent=2),
            "SCHEMA_SQL": ctx.require_schema().to_sql(), "CRUD_SQL": ctx.require_crud().to_sql(),
            "TARGET_SCHEMA": self.services.schema_manager.schema,
        }
