"""End-to-end pipeline against a real PostgreSQL with a scripted LLM (no network, no API key)."""

import json
from pathlib import Path

import psycopg
import pytest

from app.main import EXIT_OK, EXIT_PIPELINE_FAILED, run_generate
from app.observability import build_observer
from tests.fakes import shop_fixtures as shop
from tests.fakes.scripted_llm import ScriptedLLM

pytestmark = pytest.mark.integration
REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements" / "entities.json"


def run(settings, llm, capsys=None) -> int:
    observer = build_observer(settings.logs_dir, settings.secret_values())
    return run_generate(settings, REQUIREMENTS, provider=llm, observer=observer)


def test_full_pipeline_with_every_correction_loop(pg_url, make_settings, capsys):
    settings = make_settings(pg_url)
    llm = ScriptedLLM({
        "architecture.generate": [shop.bad_architecture()],
        "correction.architecture": [shop.ARCHITECTURE],
        "schema.generate": [shop.schema_script(shop.bad_schema_statements())],
        "correction.schema": [shop.schema_script()],
        "crud.generate": [shop.crud_script()],
        "testing.generate": ["Sure! Here are your tests: {not json"],
        "correction.structured_output": [shop.suite(zero_state="23505")],
        "correction.test_failure_triage": [{"diagnoses": [{
            "scenario": "Reject an order line with zero quantity", "source": "test",
            "reasoning": "zero quantity violates a CHECK (23514), not a unique constraint"}]}],
        "correction.test": [shop.fixed_zero_quantity_fix()],
    })

    assert run(settings, llm) == EXIT_OK

    out = capsys.readouterr().out
    assert "DATABASE AGENT COMPLETE" in out and "Tables: 6" in out and "CRUD functions: 8" in out
    assert "Correction successful." in out
    gen = settings.generated_dir
    for artifact in ("architecture.json", "schema.sql", "crud.sql", "test-report.json", "attempts.json", "database_state.json"):
        assert (gen / artifact).exists(), artifact
    assert sorted(p.name for p in (gen / "tests").glob("*.feature")) == ["order_placement.feature", "user_management.feature"]
    report = json.loads((gen / "test-report.json").read_text())
    assert report["failed"] == 0 and report["total"] == 11
    attempts = json.loads((gen / "attempts.json").read_text())
    assert [a["status"] for a in attempts if a["stage"] == "architecture"] == ["failed", "success"]
    assert [a["status"] for a in attempts if a["stage"] == "schema"] == ["failed", "success"]
    # The schema correction prompt carried the real PostgreSQL error and the failed SQL.
    schema_prompt = llm.prompts["correction.schema"][0]
    assert 'column "prices" does not exist' in schema_prompt and "CHECK (prices >= 0)" in schema_prompt
    # Logs exist and contain no secrets.
    log_text = next(settings.logs_dir.glob("run-*.jsonl")).read_text()
    assert "llm_request" in log_text and "sql_execution" in log_text
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT count(*) FROM agent_test_shop.users").fetchone()[0] == 0  # tests rolled back


def test_tests_reveal_crud_defect_and_crud_is_repaired(pg_url, make_settings, capsys):
    settings = make_settings(pg_url)
    llm = ScriptedLLM({
        "architecture.generate": [shop.ARCHITECTURE],
        "schema.generate": [shop.schema_script()],
        "crud.generate": [shop.crud_script(strict_update=False)],
        "testing.generate": [shop.suite(zero_state="23514", include_missing_update=True)],
        "correction.test_failure_triage": [{"diagnoses": [{
            "scenario": "Updating a missing user fails", "source": "crud",
            "reasoning": "update_user returns NULL instead of raising P0002 for a missing id",
            "suggested_fix": "raise P0002 when the UPDATE matches no row"}]}],
        "correction.crud": [shop.crud_script(strict_update=True)],
    })

    assert run(settings, llm) == EXIT_OK
    report = json.loads((settings.generated_dir / "test-report.json").read_text())
    assert report["failed"] == 0 and report["total"] == 12
    assert "P0002" in (settings.generated_dir / "crud.sql").read_text()
    assert "Updating a missing user fails" in llm.prompts["correction.crud"][0]


def test_pipeline_fails_cleanly_when_attempts_are_exhausted(pg_url, make_settings, capsys):
    settings = make_settings(pg_url, max_retries=3)
    bad = shop.schema_script(shop.bad_schema_statements())
    llm = ScriptedLLM({
        "architecture.generate": [shop.ARCHITECTURE],
        "schema.generate": [bad],
        "correction.schema": [bad, bad],
    })

    assert run(settings, llm) == EXIT_PIPELINE_FAILED

    out = capsys.readouterr().out
    assert "DATABASE AGENT FAILED" in out and "Attempt: 3 / 3" in out
    assert "crud.generate" not in llm.calls
    assert len([c for c in llm.calls if c == "correction.schema"]) == 2  # 3 attempts = 2 corrections


def test_refuses_to_touch_a_foreign_schema(pg_url, make_settings, capsys):
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS agent_foreign CASCADE")
        conn.execute("CREATE SCHEMA agent_foreign")
        conn.execute("CREATE TABLE agent_foreign.precious (id int)")
    settings = make_settings(pg_url, target_schema="agent_foreign")
    llm = ScriptedLLM({"architecture.generate": [shop.ARCHITECTURE]})

    assert run(settings, llm) == EXIT_PIPELINE_FAILED
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT count(*) FROM agent_foreign.precious").fetchone()[0] == 0
        conn.execute("DROP SCHEMA agent_foreign CASCADE")
