"""Executes BDD scenarios against PostgreSQL deterministically.

Every scenario runs inside one outer transaction that is always rolled back, and each
step runs in its own savepoint. Scenarios therefore never see each other's data and the
database is left untouched.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg import sql as pgsql

from app.models.tests import (
    BddSuite, Feature, Outcome, Scenario, ScenarioResult, Step, TestReport,
)

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class StepFailure(Exception):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(actual)
        self.expected = expected
        self.actual = actual
        self.step_label = ""


class BddRunner:
    def __init__(self, connection: psycopg.Connection) -> None:
        self._conn = connection

    def run(self, suite: BddSuite) -> TestReport:
        return TestReport(results=[self._run_scenario(f, s) for f, s in suite.scenarios()])

    def _run_scenario(self, feature: Feature, scenario: Scenario) -> ScenarioResult:
        variables: dict[str, Any] = {}
        base = dict(feature=feature.name, scenario=scenario.name, category=scenario.category.value)
        try:
            with self._conn.transaction(force_rollback=True):
                for step in scenario.steps:
                    self._run_step(step, variables)
        except StepFailure as failure:
            return ScenarioResult(**base, passed=False, failed_step=failure.step_label,
                                  expected=failure.expected, actual=failure.actual)
        return ScenarioResult(**base, passed=True)

    def _run_step(self, step: Step, variables: dict[str, Any]) -> None:
        try:
            self._execute_step(step, variables)
        except StepFailure as failure:
            failure.step_label = f"{step.keyword.value} {step.text}"
            raise

    def _execute_step(self, step: Step, variables: dict[str, Any]) -> None:
        if not step.sql:
            return
        sql_text = self._substitute(step.sql, variables)
        want_error = step.expect.outcome is Outcome.ERROR
        try:
            with self._conn.transaction():  # savepoint: a failed statement must not abort the scenario
                cur = self._conn.execute(sql_text)
                rows = cur.fetchall() if cur.description else []
                columns = [d.name for d in cur.description] if cur.description else []
        except psycopg.Error as exc:
            self._check_error(step, exc, want_error)
            return
        if want_error:
            raise StepFailure(self._describe_expected_error(step), "operation succeeded")
        self._check_rows(step, rows)
        self._capture(step, rows, columns, variables)

    def _substitute(self, sql_text: str, variables: dict[str, Any]) -> str:
        def render(match: re.Match) -> str:
            name = match.group(1)
            if name not in variables:
                raise StepFailure(f"variable '{name}' captured by an earlier step", f"variable '{name}' is not defined")
            return pgsql.Literal(variables[name]).as_string(self._conn)
        return _PLACEHOLDER.sub(render, sql_text)

    @staticmethod
    def _describe_expected_error(step: Step) -> str:
        parts = ["an error"]
        if step.expect.sqlstate:
            parts.append(f"SQLSTATE {step.expect.sqlstate}")
        if step.expect.error_contains:
            parts.append(f"containing '{step.expect.error_contains}'")
        return " ".join(parts)

    def _check_error(self, step: Step, exc: psycopg.Error, want_error: bool) -> None:
        message = exc.diag.message_primary or str(exc)
        actual = f"error [{exc.sqlstate}]: {message}"
        if not want_error:
            raise StepFailure("operation succeeds", actual)
        expect = step.expect
        if expect.sqlstate and not (exc.sqlstate or "").startswith(expect.sqlstate):
            raise StepFailure(self._describe_expected_error(step), actual)
        if expect.error_contains and expect.error_contains.lower() not in message.lower():
            raise StepFailure(self._describe_expected_error(step), actual)

    @staticmethod
    def _check_rows(step: Step, rows: list[tuple]) -> None:
        expect = step.expect
        if expect.row_count is not None and len(rows) != expect.row_count:
            raise StepFailure(f"{expect.row_count} row(s)", f"{len(rows)} row(s): {_short(rows)}")
        if expect.rows is not None and not _rows_equal(rows, expect.rows):
            raise StepFailure(f"rows {_short(expect.rows)}", f"rows {_short(rows)}")

    @staticmethod
    def _capture(step: Step, rows: list[tuple], columns: list[str], variables: dict[str, Any]) -> None:
        for name, column in step.capture.items():
            if not rows:
                raise StepFailure(f"a row to capture '{name}' from", "no rows returned")
            try:
                index = column if isinstance(column, int) else columns.index(column)
                variables[name] = rows[0][index]
            except (ValueError, IndexError):
                raise StepFailure(f"column {column!r} in result", f"result columns: {columns}") from None


def _short(value: Any, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _rows_equal(actual: list[tuple], expected: list[list[Any]]) -> bool:
    return len(actual) == len(expected) and all(
        len(a) == len(e) and all(_values_equal(x, y) for x, y in zip(a, e))
        for a, e in zip(actual, expected)
    )


def _values_equal(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float, Decimal)) and isinstance(expected, (int, float, str)):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    return str(actual) == str(expected)
