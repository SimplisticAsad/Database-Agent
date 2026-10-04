import pytest

from app.database.schema_checker import check_schema_statements
from app.database.sql_safety import SqlKind, SqlSafetyValidator
from app.errors import UnsafeSQLError
from app.models.architecture import Architecture
from tests.fakes import shop_fixtures as shop

ARCH = Architecture.model_validate(shop.ARCHITECTURE)


def problems(kind, *statements):
    return SqlSafetyValidator(kind).problems(list(statements))


@pytest.mark.parametrize("sql", [
    "DROP DATABASE x;", "drop schema public cascade;", "DROP SCHEMA IF EXISTS s CASCADE;", "CREATE SCHEMA evil;",
    "DROP TABLE users;", "TRUNCATE users;", "GRANT ALL ON users TO bob;", "CREATE ROLE r;", "ALTER SYSTEM SET x = 1;",
    "CREATE TABLE a (id int); SET search_path = public;", "COPY users TO PROGRAM 'rm -rf /';",
    "CREATE TABLE public.users (id int);", "CREATE EXTENSION dblink;", "CREATE TABLE t (id int); /* x */ DROP SCHEMA s;",
])
def test_destructive_schema_sql_is_rejected(sql):
    assert problems(SqlKind.SCHEMA, sql)


def test_comments_and_strings_cannot_hide_or_trigger_rules():
    assert problems(SqlKind.SCHEMA, "CREATE TABLE t (id int); -- harmless\n/* DROP DATABASE */ DROP DATABASE x;")
    assert not problems(SqlKind.SCHEMA, "CREATE TABLE t (note text DEFAULT 'drop database'); -- DROP SCHEMA")


def test_allowlist_per_kind():
    assert not problems(SqlKind.SCHEMA, "CREATE UNIQUE INDEX i ON t (a);", "ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0);")
    assert problems(SqlKind.SCHEMA, "INSERT INTO t VALUES (1);")
    assert problems(SqlKind.CRUD, "CREATE TABLE t (id int);")
    assert not problems(SqlKind.CRUD, "DROP FUNCTION IF EXISTS f(int);", "CREATE OR REPLACE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;")
    assert problems(SqlKind.CRUD, "DROP FUNCTION f(int);")
    assert not problems(SqlKind.TEST, "SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x", "DELETE FROM t")
    assert problems(SqlKind.TEST, "CREATE TABLE t (id int)")
    assert problems(SqlKind.TEST, "DROP TABLE t")


def test_function_bodies_are_checked_too():
    body = "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN DROP TABLE users; END $$;"
    assert problems(SqlKind.CRUD, body)
    assert problems(SqlKind.CRUD, "CREATE FUNCTION f() RETURNS void LANGUAGE plpythonu AS $$ pass $$;")
    assert problems(SqlKind.CRUD, "CREATE FUNCTION f() RETURNS void LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$;")
    assert not problems(SqlKind.CRUD, "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN INSERT INTO t VALUES (1) ON CONFLICT DO NOTHING; END $$;")


def test_check_raises_with_all_problems():
    with pytest.raises(UnsafeSQLError, match="statement 2"):
        SqlSafetyValidator(SqlKind.SCHEMA).check(["CREATE TABLE t (id int);", "DROP DATABASE x;"])


def test_empty_statement_rejected():
    assert problems(SqlKind.SCHEMA, "   ")


# --- structural schema check (foreign key ordering) ---------------------------------------------
def test_correct_schema_passes_structure_check():
    assert check_schema_statements(shop.SCHEMA_STATEMENTS, ARCH) == []


def test_reference_to_table_created_later_is_reported():
    statements = list(shop.SCHEMA_STATEMENTS)
    statements[5], statements[7] = statements[7], statements[5]  # order_items before orders
    assert any("references 'orders' which is not created before" in p for p in check_schema_statements(statements, ARCH))


def test_missing_extra_and_duplicate_tables():
    missing = [s for s in shop.SCHEMA_STATEMENTS if "TABLE categories" not in s]
    assert any("'categories' has no CREATE TABLE" in p for p in check_schema_statements(missing, ARCH))
    extra = shop.SCHEMA_STATEMENTS + ["CREATE TABLE ghosts (id int);"]
    assert any("'ghosts' is not in the architecture" in p for p in check_schema_statements(extra, ARCH))
    twice = shop.SCHEMA_STATEMENTS + [shop.SCHEMA_STATEMENTS[0]]
    assert any("created twice" in p for p in check_schema_statements(twice, ARCH))
