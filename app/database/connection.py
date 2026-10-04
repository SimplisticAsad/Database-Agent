"""Connection factory. Pins the session to the agent-owned schema and sets timeouts."""

import re

import psycopg

from app.config.settings import Settings
from app.errors import SchemaGuardError

_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_FORBIDDEN_SCHEMAS = {"public", "information_schema"}


def validate_schema_name(name: str) -> str:
    if not _SCHEMA_RE.match(name) or name in _FORBIDDEN_SCHEMAS or name.startswith("pg_"):
        raise SchemaGuardError(f"'{name}' is not an allowed target schema name")
    return name


def connect(settings: Settings, schema: str) -> psycopg.Connection:
    """Autocommit connection; transactions are always opened explicitly by callers."""
    validate_schema_name(schema)
    options = (
        f"-c search_path={schema} -c statement_timeout={settings.statement_timeout_ms} "
        "-c lock_timeout=5000 -c idle_in_transaction_session_timeout=120000"
    )
    return psycopg.connect(settings.database_url.get_secret_value(), autocommit=True, options=options)
