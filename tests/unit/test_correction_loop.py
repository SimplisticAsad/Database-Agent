import json

import pytest

from app.errors import StageFailedError
from app.observability import build_observer
from app.pipeline.context import AttemptRecord, PipelineContext
from app.pipeline.correction import CorrectionLoop, history_json
from app.models.entity import EntityRequirements


def loop(tmp_path, attempts: list, max_attempts=3):
    return CorrectionLoop("schema", "Stage 2: Schema", max_attempts, build_observer(tmp_path, []), attempts, str)


def test_success_on_first_attempt_never_calls_correct(tmp_path):
    attempts: list[AttemptRecord] = []
    result = loop(tmp_path, attempts).run("sql", lambda a: None, lambda *_: pytest.fail("should not correct"))
    assert result == "sql" and [(a.attempt, a.status) for a in attempts] == [(1, "success")]


def test_failure_then_correction_then_success(tmp_path):
    attempts: list[AttemptRecord] = []
    seen = []

    def correct(artifact, error, history):
        seen.append((artifact, error, len(history)))
        return "fixed"

    result = loop(tmp_path, attempts).run("bad", lambda a: None if a == "fixed" else "boom", correct)
    assert result == "fixed" and seen == [("bad", "boom", 0)]
    assert [(a.attempt, a.status, a.error) for a in attempts] == [(1, "failed", "boom"), (2, "success", None)]


def test_retries_are_bounded_and_history_grows(tmp_path):
    attempts: list[AttemptRecord] = []
    histories = []

    def correct(artifact, error, history):
        histories.append([r.error for r in history])
        return artifact + "!"

    with pytest.raises(StageFailedError, match="after 3 attempts") as info:
        loop(tmp_path, attempts).run("x", lambda a: f"err:{a}", correct)
    assert info.value.stage == "schema"
    assert len(attempts) == 3 and all(a.status == "failed" for a in attempts)
    assert histories == [[], ["err:x"]]  # two corrections only: attempts = corrections + 1


def test_max_attempts_one_means_no_correction(tmp_path):
    with pytest.raises(StageFailedError):
        loop(tmp_path, [], max_attempts=1).run("x", lambda a: "bad", lambda *_: pytest.fail("no correction expected"))


def test_attempts_are_logged_as_events(tmp_path):
    loop(tmp_path, []).run("x", lambda a: None, lambda *_: "y")
    events = [json.loads(l) for l in next(tmp_path.glob("run-*.jsonl")).read_text().splitlines()]
    assert events[0]["event"] == "attempt" and events[0]["status"] == "success"


def test_history_json_contains_errors_and_artifacts():
    data = json.loads(history_json([AttemptRecord("s", 1, "failed", "e1", "sql1")]))
    assert data == [{"attempt": 1, "error": "e1", "artifact": "sql1"}]


def test_pipeline_context_requires_stage_outputs():
    reqs = EntityRequirements.model_validate({"project": "p", "entities": [{"name": "A", "fields": [{"name": "id", "type": "integer"}]}]})
    ctx = PipelineContext(reqs, "p")
    assert ctx.project == "p" and ctx.errors == [] and ctx.attempts == []
    with pytest.raises(RuntimeError, match="architecture"):
        ctx.require_architecture()
    with pytest.raises(RuntimeError, match="schema"):
        ctx.require_schema()
    with pytest.raises(RuntimeError, match="CRUD"):
        ctx.require_crud()
    assert '"project"' in ctx.entity_json()
