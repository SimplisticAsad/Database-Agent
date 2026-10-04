"""PostgreSQL identifier and type rules used by deterministic validators."""

import re

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
MAX_IDENTIFIER_LENGTH = 63

# PostgreSQL reserved keywords that may not be used as unquoted table/column names.
RESERVED_WORDS = frozenset(
    """all analyse analyze and any array as asc asymmetric authorization between binary both case
    cast check collate collation column concurrently constraint create cross current_catalog
    current_date current_role current_schema current_time current_timestamp current_user default
    deferrable desc distinct do else end except false fetch for foreign freeze from full grant
    group having ilike in initially inner intersect into is isnull join lateral leading left like
    limit localtime localtimestamp natural not notnull null offset on only or order outer overlaps
    placing primary references returning right select session_user similar some symmetric
    system_user table tablesample then to trailing true union unique user using variadic verbose
    when where window with""".split()
)

_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "serial": "integer", "serial4": "integer",
    "int8": "bigint", "bigserial": "bigint", "serial8": "bigint",
    "int2": "smallint", "smallserial": "smallint",
    "bool": "boolean",
    "varchar": "text", "character varying": "text", "char": "text", "character": "text",
    "bpchar": "text", "text": "text", "citext": "text",
    "decimal": "numeric", "float8": "double precision", "float4": "real",
    "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
}


def identifier_problems(name: str) -> list[str]:
    """Return human-readable problems with an unquoted PostgreSQL identifier."""
    problems = []
    if not IDENTIFIER_RE.match(name):
        problems.append(f"'{name}' is not snake_case ([a-z_][a-z0-9_]*)")
    if len(name) > MAX_IDENTIFIER_LENGTH:
        problems.append(f"'{name}' exceeds {MAX_IDENTIFIER_LENGTH} characters")
    if name in RESERVED_WORDS:
        problems.append(f"'{name}' is a PostgreSQL reserved word")
    return problems


def normalize_type(type_name: str) -> str:
    """Reduce a type to a comparable base form (length/precision/serial stripped)."""
    base = re.sub(r"\(.*?\)", "", type_name.lower())
    base = re.split(r"\s+(?:generated|not\s+null|null|default|primary\s+key|unique|identity)\b", base, maxsplit=1)[0].strip()
    base = re.sub(r"\s+", " ", base)
    return _TYPE_ALIASES.get(base, base)


def is_array_type(type_name: str) -> bool:
    lowered = type_name.lower()
    return lowered.endswith("[]") or lowered.startswith("array") or " array" in lowered
