"""Executes generated SQL inside a single transaction and reports failures as data."""

from collections.abc import Callable
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    error: str | None = None
    sqlstate: str | None = None
    failed_statement_number: int | None = None
    failed_statement: str | None = None

    def describe(self) -> str:
        if self.success:
            return "success"
        where = f" (statement {self.failed_statement_number})" if self.failed_statement_number else ""
        state = f" [SQLSTATE {self.sqlstate}]" if self.sqlstate else ""
        statement = f"\nFailing statement:\n{self.failed_statement}" if self.failed_statement else ""
        return f"{self.error}{state}{where}{statement}"


PostCheck = Callable[[psycopg.Connection], list[str]]


class DatabaseExecutor:
    """All-or-nothing script execution: any failure rolls the whole script back."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._conn = connection

    def run_script(self, statements: list[str], post_check: PostCheck | None = None) -> ExecutionResult:
        current = 0
        try:
            with self._conn.transaction():
                for current, statement in enumerate(statements, 1):
                    self._conn.execute(statement)
                current = 0
                problems = post_check(self._conn) if post_check else []
                if problems:
                    raise _PostCheckFailed("; ".join(problems))
        except _PostCheckFailed as exc:
            return ExecutionResult(False, f"post-execution verification failed: {exc}")
        except psycopg.Error as exc:
            return self._failure(exc, statements, current)
        return ExecutionResult(True)

    @staticmethod
    def _failure(exc: psycopg.Error, statements: list[str], number: int) -> ExecutionResult:
        diag = exc.diag
        message = diag.message_primary or str(exc)
        for extra in (diag.message_detail, diag.message_hint):
            if extra:
                message += f" | {extra}"
        return ExecutionResult(
            False, message, exc.sqlstate, number or None, statements[number - 1] if number else None
        )


class _PostCheckFailed(Exception):
    pass
