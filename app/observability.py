"""Structured JSONL event log plus human-readable console output. Secrets are redacted."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Redactor:
    def __init__(self, secrets: list[str]) -> None:
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class JsonlEventLog:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self.path = path
        self._redactor = redactor
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(self._redactor.redact(line) + "\n")


class ConsoleReporter:
    def __init__(self, redactor: Redactor, stream=None) -> None:
        self._redactor = redactor
        self._stream = stream

    def _print(self, text: str) -> None:
        print(self._redactor.redact(text), file=self._stream, flush=True)

    def heading(self, text: str) -> None:
        self._print(f"\n{text}")

    def ok(self, text: str) -> None:
        self._print(f"✓ {text}")

    def info(self, text: str) -> None:
        self._print(f"  {text}")

    def fail(self, text: str) -> None:
        self._print(f"✗ {text}")


class Observer:
    """Single entry point stages use to record what happened (log file + console)."""

    def __init__(self, log: JsonlEventLog, console: ConsoleReporter, run_id: str) -> None:
        self.log, self.console, self.run_id = log, console, run_id

    def event(self, stage: str, event: str, **data: Any) -> None:
        self.log.write({
            "ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id,
            "stage": stage, "event": event, **data,
        })

    # Console helpers also write an event so the log is a superset of the console.
    def heading(self, stage: str, text: str) -> None:
        self.console.heading(text)
        self.event(stage, "stage_started", title=text)

    def ok(self, stage: str, text: str, **data: Any) -> None:
        self.console.ok(text)
        self.event(stage, "ok", message=text, **data)

    def info(self, stage: str, text: str, **data: Any) -> None:
        self.console.info(text)
        self.event(stage, "info", message=text, **data)

    def fail(self, stage: str, text: str, **data: Any) -> None:
        self.console.fail(text)
        self.event(stage, "failed", message=text, **data)


def build_observer(logs_dir: Path, secrets: list[str], run_id: str | None = None, stream=None) -> Observer:
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    redactor = Redactor(secrets)
    log = JsonlEventLog(logs_dir / f"run-{run_id}.jsonl", redactor)
    return Observer(log, ConsoleReporter(redactor, stream), run_id)
