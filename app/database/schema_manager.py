"""Lifecycle of the single PostgreSQL schema the agent is allowed to own."""

import psycopg
from psycopg import sql

from app.database.connection import validate_schema_name
from app.errors import SchemaGuardError

OWNERSHIP_MARKER = "managed by database-agent"


class SchemaManager:
    """The only component allowed to DROP/CREATE SCHEMA - never the LLM."""

    def __init__(self, connection: psycopg.Connection, schema: str) -> None:
        self._conn = connection
        self.schema = validate_schema_name(schema)

    def server_version(self) -> str:
        return self._conn.execute("SHOW server_version").fetchone()[0]

    def reset(self) -> str:
        """Create the schema, or recreate it if (and only if) the agent created it before."""
        state = self._inspect()
        if state == "agent_owned":
            self._conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
            self._create()
            return "reset"
        if state == "foreign":
            raise SchemaGuardError(
                f"Schema '{self.schema}' exists but is not managed by the agent and is not empty. "
                "Choose another TARGET_SCHEMA; the agent will not modify it."
            )
        if state == "empty_foreign":
            self._comment()
            return "adopted"
        self._create()
        return "created"

    def _inspect(self) -> str:
        row = self._conn.execute(
            "SELECT obj_description(n.oid, 'pg_namespace'), "
            "(SELECT count(*) FROM pg_class c WHERE c.relnamespace = n.oid) + "
            "(SELECT count(*) FROM pg_proc p WHERE p.pronamespace = n.oid) "
            "FROM pg_namespace n WHERE n.nspname = %s",
            (self.schema,),
        ).fetchone()
        if row is None:
            return "missing"
        comment, object_count = row
        if comment == OWNERSHIP_MARKER:
            return "agent_owned"
        return "empty_foreign" if object_count == 0 else "foreign"

    def _create(self) -> None:
        self._conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self._comment()

    def _comment(self) -> None:
        self._conn.execute(
            sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                sql.Identifier(self.schema), sql.Literal(OWNERSHIP_MARKER)
            )
        )
