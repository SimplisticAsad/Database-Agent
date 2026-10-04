"""Read-only catalog inspection: live database state for prompts and verification."""

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.models.architecture import Architecture

_COLUMNS = """
SELECT table_name, column_name, data_type, is_nullable = 'YES' AS nullable, column_default
FROM information_schema.columns WHERE table_schema = %s ORDER BY table_name, ordinal_position
"""
_CONSTRAINTS = """
SELECT c.relname AS table_name, con.conname AS name, con.contype AS type,
  pg_get_constraintdef(con.oid) AS definition,
  ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum ORDER BY k.ord) AS columns,
  rc.relname AS ref_table,
  ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum ORDER BY k.ord) AS ref_columns
FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_class rc ON rc.oid = con.confrelid
WHERE n.nspname = %s ORDER BY c.relname, con.conname
"""
_INDEXES = "SELECT tablename AS table_name, indexname AS name, indexdef AS definition FROM pg_indexes WHERE schemaname = %s ORDER BY 1, 2"
_FUNCTIONS = """
SELECT p.proname AS name, pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_function_result(p.oid) AS returns, CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END AS kind
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = %s AND p.prokind IN ('f', 'p') ORDER BY p.proname
"""

_CONSTRAINT_TYPES = {"p": "PRIMARY KEY", "f": "FOREIGN KEY", "u": "UNIQUE", "c": "CHECK", "x": "EXCLUDE"}


class DatabaseInspector:
    def __init__(self, connection: psycopg.Connection, schema: str) -> None:
        self._conn = connection
        self._schema = schema

    def snapshot(self) -> dict[str, Any]:
        """JSON-serialisable description of tables, constraints, indexes and functions."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            columns, constraints = self._rows(cur, _COLUMNS), self._rows(cur, _CONSTRAINTS)
            indexes, functions = self._rows(cur, _INDEXES), self._rows(cur, _FUNCTIONS)
        tables: dict[str, dict[str, list]] = {}
        for kind, rows in (("columns", columns), ("constraints", constraints), ("indexes", indexes)):
            for row in rows:
                entry = tables.setdefault(row.pop("table_name"), {"columns": [], "constraints": [], "indexes": []})
                if kind == "constraints":
                    row["type"] = _CONSTRAINT_TYPES.get(row["type"], row["type"])
                entry[kind].append(row)
        return {"schema": self._schema, "tables": tables, "functions": functions}

    def _rows(self, cur, query: str) -> list[dict]:
        return cur.execute(query, (self._schema,)).fetchall()


def verify_schema_against_architecture(snapshot: dict[str, Any], architecture: Architecture) -> list[str]:
    """Compare what PostgreSQL actually built with what the architecture promised."""
    problems: list[str] = []
    actual = snapshot["tables"]
    expected = {t.name for t in architecture.tables}
    for name in sorted(expected - set(actual)):
        problems.append(f"table '{name}' was not created")
    for name in sorted(set(actual) - expected):
        problems.append(f"unexpected table '{name}' was created (not in architecture)")
    for table in architecture.tables:
        if table.name in actual:
            problems.extend(_verify_table(table, actual[table.name]))
    return problems


def _verify_table(table, actual: dict[str, list]) -> list[str]:
    problems = []
    names = {c["column_name"] for c in actual["columns"]}
    for col in table.columns:
        if col.name not in names:
            problems.append(f"{table.name}: column '{col.name}' missing")
    pks = [c["columns"] for c in actual["constraints"] if c["type"] == "PRIMARY KEY"]
    if [table.primary_key.columns] != pks:
        problems.append(f"{table.name}: primary key is {pks}, expected {table.primary_key.columns}")
    actual_fks = {(tuple(c["columns"]), c["ref_table"], tuple(c["ref_columns"]))
                  for c in actual["constraints"] if c["type"] == "FOREIGN KEY"}
    for fk in table.foreign_keys:
        if (tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns)) not in actual_fks:
            problems.append(f"{table.name}: foreign key {fk.columns} -> {fk.ref_table}{fk.ref_columns} missing")
    return problems
