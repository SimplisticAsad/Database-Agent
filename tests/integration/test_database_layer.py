"""Executor, schema guard, introspection and BDD runner against a real PostgreSQL."""

import psycopg
import pytest

from app.database.bdd_runner import BddRunner
from app.database.connection import connect, validate_schema_name
from app.database.executor import DatabaseExecutor
from app.database.introspection import DatabaseInspector, verify_schema_against_architecture
from app.database.schema_manager import SchemaManager
from app.errors import SchemaGuardError
from app.models.architecture import Architecture
from app.models.tests import BddSuite
from tests.fakes import shop_fixtures as shop

pytestmark = pytest.mark.integration
SCHEMA = "agent_test_db_layer"


@pytest.fixture
def db(pg_url, make_settings):
    settings = make_settings(pg_url, target_schema=SCHEMA)
    conn = connect(settings, SCHEMA)
    manager = SchemaManager(conn, SCHEMA)
    manager.reset()
    yield conn, manager
    conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.close()


def test_schema_manager_creates_resets_and_guards(db, pg_url):
    conn, manager = db
    conn.execute("CREATE TABLE t (id int)")
    assert manager.reset() == "reset"
    assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = %s", (SCHEMA,)).fetchone()[0] == 0
    for bad in ("public", "pg_catalog", "information_schema", "Bad-Name"):
        with pytest.raises(SchemaGuardError):
            validate_schema_name(bad)
    assert "." in manager.server_version() or manager.server_version()


def test_script_is_atomic_and_errors_are_structured(db):
    conn, _ = db
    executor = DatabaseExecutor(conn)
    result = executor.run_script(["CREATE TABLE a (id int)", "CREATE TABLE b (x int CHECK (nope > 1))"])
    assert not result.success and result.sqlstate == "42703" and result.failed_statement_number == 2
    assert "nope" in result.error and "CREATE TABLE b" in result.describe()
    assert conn.execute("SELECT to_regclass('a')").fetchone()[0] is None  # statement 1 rolled back
    assert executor.run_script(["CREATE TABLE a (id int)"]).success
    assert conn.execute("SELECT to_regclass('a')").fetchone()[0] is not None  # committed (durable)


def test_post_check_failure_rolls_back(db):
    conn, _ = db
    result = DatabaseExecutor(conn).run_script(["CREATE TABLE a (id int)"], post_check=lambda _c: ["not what I wanted"])
    assert not result.success and "not what I wanted" in result.error
    assert conn.execute("SELECT to_regclass('a')").fetchone()[0] is None


def test_search_path_confines_unqualified_objects(db):
    conn, _ = db
    DatabaseExecutor(conn).run_script(["CREATE TABLE confined (id int)"])
    assert conn.execute("SELECT schemaname FROM pg_tables WHERE tablename = 'confined'").fetchone()[0] == SCHEMA


def test_introspection_verifies_architecture(db):
    conn, _ = db
    arch = Architecture.model_validate(shop.ARCHITECTURE)
    inspector = DatabaseInspector(conn, SCHEMA)
    assert any("was not created" in p for p in verify_schema_against_architecture(inspector.snapshot(), arch))
    assert DatabaseExecutor(conn).run_script(shop.SCHEMA_STATEMENTS).success
    snapshot = inspector.snapshot()
    assert verify_schema_against_architecture(snapshot, arch) == []
    fks = [c for c in snapshot["tables"]["order_items"]["constraints"] if c["type"] == "FOREIGN KEY"]
    assert {(f["ref_table"], tuple(f["columns"])) for f in fks} == {("orders", ("order_id",)), ("products", ("product_id",))}


def test_verification_detects_missing_foreign_key(db):
    conn, _ = db
    statements = [s.replace(", CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT", "")
                  for s in shop.SCHEMA_STATEMENTS]
    DatabaseExecutor(conn).run_script(statements)
    problems = verify_schema_against_architecture(DatabaseInspector(conn, SCHEMA).snapshot(), Architecture.model_validate(shop.ARCHITECTURE))
    assert any("orders: foreign key ['user_id'] -> users" in p for p in problems)


@pytest.fixture
def shop_db(db):
    conn, _ = db
    executor = DatabaseExecutor(conn)
    assert executor.run_script(shop.SCHEMA_STATEMENTS).success and executor.run_script(shop.crud_statements()).success
    return conn


def test_all_example_scenarios_pass_and_leave_database_untouched(shop_db):
    report = BddRunner(shop_db).run(BddSuite.model_validate(shop.suite(zero_state="23514")))
    assert report.failed == 0, report.render()
    assert shop_db.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def run_one(conn, *steps, name="s"):
    suite = BddSuite.model_validate({"features": [{"name": "f", "scenarios": [shop.scenario(name, "happy_path", *steps)]}]})
    return BddRunner(conn).run(suite).results[0]


def test_runner_reports_expected_vs_actual(shop_db):
    ok = {"outcome": "error", "sqlstate": "23505"}
    dup = run_one(shop_db, shop.step("Given", "u", "SELECT * FROM create_user('a@x.com','A')"),
                  shop.step("When", "dup succeeds?", "SELECT * FROM create_user('b@x.com','B')", expect=ok))
    assert not dup.passed and dup.expected == "an error SQLSTATE 23505" and dup.actual == "operation succeeded"
    assert dup.failed_step == "When dup succeeds?"

    wrong_state = run_one(shop_db, shop.step("When", "null name", "SELECT * FROM create_user('a@x.com', NULL)", expect=ok))
    assert not wrong_state.passed and "23502" in wrong_state.actual

    rows = run_one(shop_db, shop.step("Then", "count", "SELECT count(*) FROM users", expect={"rows": [[5]]}))
    assert not rows.passed and "[(0,)]" in rows.actual

    unexpected = run_one(shop_db, shop.step("When", "bad sql", "SELECT * FROM nonexistent"))
    assert not unexpected.passed and "does not exist" in unexpected.actual


def test_runner_variables_numeric_and_error_contains(shop_db):
    result = run_one(
        shop_db,
        shop.step("Given", "p", "SELECT id, price FROM create_product('Pen', 10.5, 3)", capture={"pid": "id", "price": 1}),
        shop.step("Then", "numeric equality ignores scale", "SELECT {{price}}", expect={"rows": [[10.5]]}),
        shop.step("And", "named capture is usable", "SELECT stock FROM products WHERE id = {{pid}}", expect={"row_count": 1, "rows": [[3]]}),
        shop.step("And", "message match", "SELECT * FROM create_product('x', -1, 1)",
                  expect={"outcome": "error", "sqlstate": "23", "error_contains": "ck_products_price"}),
    )
    assert result.passed, result

    missing = run_one(shop_db, shop.step("When", "uses undefined var", "SELECT {{nope}}"))
    assert not missing.passed and "not defined" in missing.actual
    no_rows = run_one(shop_db, shop.step("Given", "x", "SELECT id FROM users", capture={"u": 0}))
    assert not no_rows.passed and "no rows" in no_rows.actual


def test_scenarios_are_isolated_from_each_other(shop_db):
    scenario_a = shop.scenario("a", "happy_path", shop.step("Given", "user", "SELECT * FROM create_user('a@x.com','A')"))
    scenario_b = shop.scenario("b", "happy_path", shop.step("Then", "empty again", "SELECT count(*) FROM users", expect={"rows": [[0]]}))
    suite = BddSuite.model_validate({"features": [{"name": "f", "scenarios": [scenario_a, scenario_b]}]})
    assert BddRunner(shop_db).run(suite).failed == 0
