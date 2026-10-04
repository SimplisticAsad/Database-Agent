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


def test_openai_compatible_provider_selection_and_secret_handling(make_settings):
    from app.llm.provider import OpenAICompatibleProvider
    from app.errors import LLMError
    with pytest.raises(LLMError, match="LLM_API_KEY is not set"):
        create_provider(make_settings(llm_provider="openai_compatible"))
    settings = make_settings(llm_provider="openai_compatible", llm_api_key="AIza-secret-key", llm_model="m")
    assert isinstance(create_provider(settings), OpenAICompatibleProvider)
    assert "AIza-secret-key" in settings.secret_values() and "AIza-secret-key" not in repr(settings)


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def create(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(outcomes, sleeps, **kwargs):
    import types
    from app.llm.provider import OpenAICompatibleProvider
    p = OpenAICompatibleProvider("m", "k", "http://x", 100, 1.0, sleep=sleeps.append, clock=lambda: 0.0, **kwargs)
    p._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_FakeCompletions(outcomes)))
    return p


def _rate_limit(message="quota. Please retry in 2.5s."):
    import httpx2 as httpx, openai
    request = httpx.Request("POST", "http://x")
    return openai.RateLimitError(message, response=httpx.Response(429, request=request), body=None)


def _ok(text="{}"):
    import types
    msg = types.SimpleNamespace(content=text)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])


def test_rate_limit_is_retried_honouring_server_hint():
    sleeps: list[float] = []
    p = _provider([_rate_limit(), _rate_limit("no hint"), _ok("done")], sleeps)
    assert p.generate("hi") == "done"
    assert sleeps == [3.5, 10.0]  # "retry in 2.5s" + 1s margin, then exponential fallback


def test_rate_limit_retries_are_bounded():
    from app.errors import LLMError
    sleeps: list[float] = []
    p = _provider([_rate_limit()] * 3, sleeps, rate_limit_retries=2)
    with pytest.raises(LLMError, match="LLM API error"):
        p.generate("hi")
    assert len(sleeps) == 2


def test_min_interval_throttles_consecutive_calls():
    sleeps: list[float] = []
    p = _provider([_ok(), _ok()], sleeps, min_interval=13.0)
    p.generate("a"); p.generate("b")
    assert sleeps == [13.0]  # first call free, second waits (clock is frozen at 0)


def test_server_overload_503_is_retried_like_a_rate_limit():
    import httpx2 as httpx, openai
    request = httpx.Request("POST", "http://x")
    overload = openai.InternalServerError("high demand", response=httpx.Response(503, request=request), body=None)
    sleeps: list[float] = []
    assert _provider([overload, _ok("fine")], sleeps).generate("hi") == "fine"
    assert sleeps == [5.0]


def test_daily_quota_fails_fast_without_sleeping():
    from app.errors import LLMError
    sleeps: list[float] = []
    p = _provider([_rate_limit("GenerateRequestsPerDayPerProjectPerModel-FreeTier")], sleeps)
    with pytest.raises(LLMError, match="Daily quota exhausted"):
        p.generate("hi")
    assert sleeps == []
