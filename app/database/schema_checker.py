"""Pre-execution structural check of generated DDL against the architecture."""

import re

from app.database.sql_safety import strip_comments_and_strings
from app.models.architecture import Architecture

_CREATE_TABLE = re.compile(r"^\s*create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)\s*\(")
_REFERENCES = re.compile(r"\breferences\s+([a-z_][a-z0-9_]*)")


def check_schema_statements(statements: list[str], architecture: Architecture) -> list[str]:
    """Verify created tables match the architecture and inline REFERENCES resolve in order."""
    problems: list[str] = []
    created: list[str] = []
    for number, statement in enumerate(statements, 1):
        cleaned = strip_comments_and_strings(statement)
        match = _CREATE_TABLE.match(cleaned)
        if not match:
            if re.match(r"\s*create\s+table", cleaned):
                problems.append(f"statement {number}: cannot parse CREATE TABLE target (use unqualified snake_case names)")
            continue
        name = match.group(1)
        if name in created:
            problems.append(f"statement {number}: table '{name}' is created twice")
        for target in _REFERENCES.findall(cleaned):
            if target != name and target not in created:
                problems.append(
                    f"statement {number}: table '{name}' references '{target}' which is not created before it "
                    "(reorder, or add the foreign key with ALTER TABLE after both tables exist)"
                )
        created.append(name)
    expected = {t.name for t in architecture.tables}
    for name in sorted(expected - set(created)):
        problems.append(f"architecture table '{name}' has no CREATE TABLE statement")
    for name in sorted(set(created) - expected):
        problems.append(f"CREATE TABLE for '{name}' is not in the architecture")
    return problems
