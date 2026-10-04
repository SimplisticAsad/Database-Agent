import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.errors import StructuredOutputError
from app.llm.client import LLMClient, extract_json, parse_model
from app.llm.prompt_loader import PromptLoader
from app.llm.provider import create_provider
from app.models.schema import SchemaScript
from app.observability import build_observer
from tests.fakes.scripted_llm import ScriptedLLM

PROMPTS = Path(__file__).resolve().parents[2] / "app" / "prompts"
GOOD = {"statements": ["CREATE TABLE t (id int);"]}
SCHEMA_VARS = {"PROJECT_NAME": "p", "ENTITY_JSON": "{}", "POSTGRES_VERSION": "16", "ARCHITECTURE_JSON": "{}",
               "TARGET_SCHEMA": "s", "DATABASE_STATE_JSON": "{}"}


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go:\n{"a": {"b": 2}}\nHope it helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_parse_model_validates_shape():
    assert parse_model(json.dumps(GOOD), SchemaScript).statements == ["CREATE TABLE t (id int);"]
    with pytest.raises(ValidationError):
        parse_model('{"statements": []}', SchemaScript)
    with pytest.raises(ValidationError):
        parse_model('{"statements": ["x"], "extra": 1}', SchemaScript)


def client(tmp_path, script, attempts=3):
    llm = ScriptedLLM(script)
    observer = build_observer(tmp_path, [])
    return LLMClient(llm, PromptLoader(PROMPTS), observer, attempts), llm


def test_malformed_json_triggers_structured_correction(tmp_path):
    c, llm = client(tmp_path, {"schema.generate": ["{broken"], "correction.structured_output": ['{"statements": 5}', GOOD]})
    result = c.generate_model("schema", "schema/generate_schema", SCHEMA_VARS, SchemaScript)
    assert result.statements == GOOD["statements"]
    assert llm.calls == ["schema.generate", "correction.structured_output", "correction.structured_output"]
    assert "Expecting property name" in llm.prompts["correction.structured_output"][0]


def test_structured_correction_is_bounded(tmp_path):
    c, llm = client(tmp_path, {"schema.generate": ["x"], "correction.structured_output": ["y", "z"]}, attempts=3)
    with pytest.raises(StructuredOutputError):
        c.generate_model("schema", "schema/generate_schema", SCHEMA_VARS, SchemaScript)
    assert len(llm.calls) == 3


def test_requests_and_responses_are_logged(tmp_path):
    c, _ = client(tmp_path, {"schema.generate": [GOOD]})
    c.generate_model("schema", "schema/generate_schema", SCHEMA_VARS, SchemaScript)
    events = [json.loads(line) for line in next(tmp_path.glob("run-*.jsonl")).read_text().splitlines()]
    assert [e["event"] for e in events] == ["llm_request", "llm_response"]
    assert "CREATE TABLE" in events[1]["response"]


def test_unknown_provider_is_rejected(make_settings):
    from app.errors import LLMError
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        create_provider(make_settings(llm_provider="mystery"))


def test_missing_api_key_fails_before_any_database_work(make_settings, monkeypatch):
    from app.errors import LLMError
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        create_provider(make_settings())
