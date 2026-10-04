import io
import json

from app.observability import Redactor, build_observer


def test_redactor_removes_secrets():
    assert Redactor(["hunter22", "sk-ant-abc"]).redact("pw=hunter22 key sk-ant-abc") == "pw=*** key ***"


def test_secrets_never_reach_logs_or_console(tmp_path):
    stream = io.StringIO()
    obs = build_observer(tmp_path, ["s3cr3t-pass"], stream=stream)
    obs.event("schema", "llm_request", prompt="connect with s3cr3t-pass")
    obs.fail("schema", "error for s3cr3t-pass")
    assert "s3cr3t-pass" not in next(tmp_path.glob("run-*.jsonl")).read_text()
    assert "s3cr3t-pass" not in stream.getvalue() and "***" in stream.getvalue()


def test_event_records_have_required_fields(tmp_path):
    obs = build_observer(tmp_path, [], run_id="r1")
    obs.event("crud", "attempt", attempt=2)
    record = json.loads(next(tmp_path.glob("run-r1.jsonl")).read_text())
    assert {"ts", "run_id", "stage", "event", "attempt"} <= record.keys()


def test_settings_secret_handling(make_settings):
    s = make_settings("postgresql://agent:topsecret@db:5432/x", anthropic_api_key="sk-ant-key123")
    assert "topsecret" not in s.redacted_database_url()
    assert {"topsecret", "sk-ant-key123"} <= set(s.secret_values())
    assert "topsecret" not in repr(s) and "sk-ant-key123" not in repr(s)
