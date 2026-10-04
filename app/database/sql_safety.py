"""Static safety checks on LLM-generated SQL. Runs before anything touches PostgreSQL."""

import re
from dataclasses import dataclass
from enum import Enum

from app.errors import UnsafeSQLError


class SqlKind(str, Enum):
    SCHEMA = "schema"
    CRUD = "crud"
    TEST = "test"


_ALLOWED_START = {
    SqlKind.SCHEMA: [
        r"create\s+table", r"alter\s+table", r"create\s+(unique\s+)?index", r"create\s+type",
        r"create\s+sequence", r"comment\s+on", r"create\s+(or\s+replace\s+)?function",
        r"create\s+(or\s+replace\s+)?trigger",
    ],
    SqlKind.CRUD: [
        r"create\s+(or\s+replace\s+)?(function|procedure)",
        r"drop\s+(function|procedure)\s+if\s+exists",
    ],
    SqlKind.TEST: [r"select", r"insert", r"update", r"delete", r"with", r"call"],
}

# Rejected in every kind of SQL, anywhere in the statement (including function bodies).
_FORBIDDEN = [
    (r"\bdrop\s+(database|schema|role|user|extension|owned|table|view|trigger|type|index)\b", "destructive DROP"),
    (r"\bcreate\s+(database|schema|role|user|extension|server|tablespace)\b", "creates objects outside the table layer"),
    (r"\balter\s+(database|system|role|user|schema|default\s+privileges|extension)\b", "alters instance-level objects"),
    (r"\b(grant|revoke)\s", "privilege changes"),
    (r"\btruncate\b", "TRUNCATE"),
    (r"\bcopy\s+\S+\s+(from|to)\b", "COPY"),
    (r"\bset\s+(session\s+|local\s+)?(search_path|role|session\s+authorization)\b", "changes search_path/role"),
    (r"\breset\s+(search_path|role|all|session)\b", "RESET"),
    (r"\bsecurity\s+definer\b", "SECURITY DEFINER"),
    (r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|dblink|set_config|"
     r"pg_terminate_backend|pg_cancel_backend|pg_sleep)\b", "dangerous built-in function"),
    (r"\bpublic\s*\.", "reference to the public schema"),
    (r"\blanguage\s+(?!sql\b|plpgsql\b)\w+", "unsupported procedural language"),
]

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def strip_comments_and_strings(sql: str) -> str:
    """Remove comments and single-quoted literals (keeps dollar-quoted bodies)."""
    sql = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))
    return _STRING_LITERAL.sub("''", sql).lower()


@dataclass(frozen=True)
class SqlSafetyValidator:
    kind: SqlKind

    def problems(self, statements: list[str]) -> list[str]:
        found: list[str] = []
        for number, statement in enumerate(statements, 1):
            found.extend(self._statement_problems(number, statement))
        return found

    def check(self, statements: list[str]) -> None:
        problems = self.problems(statements)
        if problems:
            raise UnsafeSQLError("; ".join(problems))

    def _statement_problems(self, number: int, statement: str) -> list[str]:
        cleaned = strip_comments_and_strings(statement).strip()
        label = f"statement {number}"
        if not cleaned:
            return [f"{label}: empty statement"]
        problems = []
        if not any(re.match(pattern, cleaned) for pattern in _ALLOWED_START[self.kind]):
            problems.append(f"{label}: not allowed in {self.kind.value} SQL (starts with '{cleaned[:30]}')")
        for pattern, reason in _FORBIDDEN:
            if re.search(pattern, cleaned + " "):
                problems.append(f"{label}: forbidden - {reason}")
        return problems
