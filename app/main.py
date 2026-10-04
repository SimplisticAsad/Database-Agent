"""CLI entry point:  python -m app.main generate requirements/entities.json"""

import argparse
import sys
from pathlib import Path

import psycopg

from app.composition import build_pipeline
from app.config.settings import Settings, get_settings
from app.errors import DatabaseAgentError, RequirementsError
from app.llm.base import LLMProvider
from app.llm.provider import create_provider
from app.models.entity import load_requirements
from app.observability import Observer, build_observer
from app.pipeline.orchestrator import PipelineResult

EXIT_OK, EXIT_PIPELINE_FAILED, EXIT_BAD_INPUT, EXIT_TESTS_FAILED = 0, 1, 2, 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="database-agent", description="Entity JSON -> PostgreSQL database")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="run the full pipeline")
    generate.add_argument("requirements", type=Path, help="path to entities.json")
    generate.add_argument("--max-retries", type=int, help="override MAX_RETRIES (total attempts per stage)")
    generate.add_argument("--schema", help="override TARGET_SCHEMA (agent-owned PostgreSQL schema)")
    return parser.parse_args(argv)


def run_generate(settings: Settings, requirements_path: Path, provider: LLMProvider | None = None,
                 observer: Observer | None = None) -> int:
    observer = observer or build_observer(settings.logs_dir, settings.secret_values())
    console = observer.console
    console.heading("Loading requirements...")
    try:
        requirements = load_requirements(requirements_path)
    except RequirementsError as exc:
        console.fail(f"Requirements rejected: {exc}")
        return EXIT_BAD_INPUT
    console.ok("Requirements validated")
    try:
        provider = provider or create_provider(settings)
        pipeline = build_pipeline(settings, provider, observer, requirements)
    except (DatabaseAgentError, psycopg.Error) as exc:
        console.fail(f"Cannot start pipeline: {exc}")
        return EXIT_BAD_INPUT
    try:
        result = pipeline.orchestrator.run(pipeline.context)
    finally:
        pipeline.close()
    print_summary(console, result, settings)
    if not result.completed:
        return EXIT_PIPELINE_FAILED
    return EXIT_OK if result.tests_passed else EXIT_TESTS_FAILED


def print_summary(console, result: PipelineResult, settings: Settings) -> None:
    ctx = result.context
    bar = "=" * 32
    if not result.completed:
        console.heading(f"{bar}\nDATABASE AGENT FAILED\n{bar}")
        console.info(f"Stage: {result.failed_stage}")
        console.info(f"Reason: {result.error}")
        console.info(f"Artifacts: {settings.generated_dir}/   Logs: {settings.logs_dir}/")
        return
    report = ctx.test_report
    console.heading(f"{bar}\nDATABASE AGENT COMPLETE\n{bar}")
    console.info(f"Tables: {len(ctx.require_architecture().tables)}")
    console.info(f"CRUD functions: {len(ctx.require_crud().functions)}")
    console.info(f"BDD tests: {report.total}")
    console.info(f"Passed: {report.passed}")
    console.info(f"Failed: {report.failed}")
    if report.failures:
        console.info("")
        for line in report.render().splitlines():
            console.info(line)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    updates = {k: v for k, v in (("max_retries", args.max_retries), ("target_schema", args.schema)) if v}
    if updates:
        settings = settings.model_copy(update=updates)
    return run_generate(settings, args.requirements)


if __name__ == "__main__":
    sys.exit(main())
