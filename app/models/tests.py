"""BDD test suite contract: Gherkin-style scenarios whose steps carry executable SQL."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioCategory(str, Enum):
    HAPPY_PATH = "happy_path"
    CONSTRAINT = "constraint_violation"
    RELATIONSHIP = "relationship"
    TRANSACTION = "transaction"
    BOUNDARY = "boundary"
    LIFECYCLE = "lifecycle"


class Keyword(str, Enum):
    GIVEN = "Given"
    WHEN = "When"
    THEN = "Then"
    AND = "And"
    BUT = "But"


class Outcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Outcome = Outcome.SUCCESS
    sqlstate: str | None = Field(
        default=None, description="Expected SQLSTATE (5 chars) or class (2 chars) when outcome=error"
    )
    error_contains: str | None = None
    rows: list[list[Any]] | None = Field(default=None, description="Exact result rows (use ORDER BY)")
    row_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "Expectation":
        if self.outcome is Outcome.ERROR and (self.rows is not None or self.row_count is not None):
            raise ValueError("rows/row_count cannot be combined with outcome=error")
        if self.outcome is Outcome.SUCCESS and (self.sqlstate or self.error_contains):
            raise ValueError("sqlstate/error_contains require outcome=error")
        if self.sqlstate is not None and len(self.sqlstate) not in (2, 5):
            raise ValueError("sqlstate must be 2 or 5 characters")
        return self


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: Keyword
    text: str = Field(min_length=1)
    sql: str | None = Field(default=None, description="Executable SQL; may use {{variable}} placeholders")
    expect: Expectation = Field(default_factory=Expectation)
    capture: dict[str, int | str] = Field(
        default_factory=dict,
        description="variable -> result column (index or name) taken from the first row",
    )


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    category: ScenarioCategory
    steps: list[Step] = Field(min_length=1)


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str = ""
    scenarios: list[Scenario] = Field(min_length=1)


class BddSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: list[Feature] = Field(min_length=1)

    def scenarios(self) -> list[tuple[Feature, Scenario]]:
        return [(f, s) for f in self.features for s in f.scenarios]

    @model_validator(mode="after")
    def _unique_scenarios(self) -> "BddSuite":
        names = [s.name for _, s in self.scenarios()]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate scenario names: {sorted(dupes)}")
        return self


class ScenarioResult(BaseModel):
    feature: str
    scenario: str
    category: str
    passed: bool
    failed_step: str | None = None
    expected: str | None = None
    actual: str | None = None


class TestReport(BaseModel):
    __test__ = False  # not a pytest class
    results: list[ScenarioResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def failures(self) -> list[ScenarioResult]:
        return [r for r in self.results if not r.passed]

    def render(self) -> str:
        lines = [
            "DATABASE AGENT TEST REPORT", "",
            f"Total tests: {self.total}", f"Passed: {self.passed}", f"Failed: {self.failed}",
        ]
        if self.failures:
            lines += ["", "Failures:", ""]
            for i, r in enumerate(self.failures, 1):
                lines += [
                    f"{i}. {r.scenario}  [{r.category}]",
                    f"   Step:     {r.failed_step}",
                    f"   Expected: {r.expected}",
                    f"   Actual:   {r.actual}",
                ]
        return "\n".join(lines) + "\n"


class FailureSource(str, Enum):
    SCHEMA = "schema"
    CRUD = "crud"
    TEST = "test"


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str
    source: FailureSource
    reasoning: str
    suggested_fix: str = ""


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnoses: list[Diagnosis]
