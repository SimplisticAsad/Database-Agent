import pytest
from pydantic import ValidationError

from app.models.tests import BddSuite, Expectation, TestReport, ScenarioResult
from app.pipeline.testing_stage import merge_scenarios, render_feature, suite_problems
from tests.fakes import shop_fixtures as shop


def test_example_suite_is_statically_clean():
    assert suite_problems(BddSuite.model_validate(shop.suite())) == []


def test_unsafe_test_sql_and_uncaptured_variable_are_reported():
    bad = shop.suite()
    bad["features"][0]["scenarios"][0]["steps"][0]["sql"] = "DROP TABLE users"
    bad["features"][0]["scenarios"][1]["steps"][1]["sql"] = "SELECT {{ghost}}"
    problems = suite_problems(BddSuite.model_validate(bad))
    assert any("not allowed in test SQL" in p for p in problems)
    assert any("'ghost' used before it is captured" in p for p in problems)


def test_duplicate_scenario_names_rejected():
    data = shop.suite()
    data["features"][1]["scenarios"].append(data["features"][0]["scenarios"][0])
    with pytest.raises(ValidationError, match="duplicate scenario"):
        BddSuite.model_validate(data)


@pytest.mark.parametrize("kwargs", [
    {"outcome": "error", "rows": [[1]]}, {"outcome": "success", "sqlstate": "23505"}, {"outcome": "error", "sqlstate": "234"},
])
def test_inconsistent_expectations_rejected(kwargs):
    with pytest.raises(ValidationError):
        Expectation(**kwargs)


def test_merge_replaces_by_name_and_reports_unmatched():
    suite = BddSuite.model_validate(shop.suite(zero_state="23505"))
    fixes = BddSuite.model_validate(shop.fixed_zero_quantity_fix())
    fixes.features[0].scenarios.append(fixes.features[0].scenarios[0].model_copy(update={"name": "Brand new"}))
    merged, unmatched = merge_scenarios(suite, fixes)
    assert unmatched == ["Brand new"]
    fixed = next(s for _, s in merged.scenarios() if s.name == "Reject an order line with zero quantity")
    assert fixed.steps[-1].expect.sqlstate == "23514"
    assert len(merged.scenarios()) == len(suite.scenarios())


def test_feature_rendering_is_gherkin():
    text = render_feature(BddSuite.model_validate(shop.suite()).features[0])
    assert text.startswith("Feature: User management") and "Scenario: Create a valid user" in text and "    When I create a user" in text


def test_report_rendering():
    report = TestReport(results=[
        ScenarioResult(feature="f", scenario="ok", category="happy_path", passed=True),
        ScenarioResult(feature="f", scenario="dup_email", category="constraint_violation", passed=False,
                       failed_step="When create", expected="an error SQLSTATE 23505", actual="operation succeeded")])
    text = report.render()
    assert "Total tests: 2" in text and "Passed: 1" in text and "Failed: 1" in text
    assert "1. dup_email" in text and "Expected: an error SQLSTATE 23505" in text and "Actual:   operation succeeded" in text
