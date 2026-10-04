"""Composition root: wires settings, LLM, database and stages together."""

from dataclasses import dataclass

import psycopg

from app.artifacts import ArtifactStore
from app.config.settings import Settings
from app.database.bdd_runner import BddRunner
from app.database.connection import connect
from app.database.executor import DatabaseExecutor
from app.database.introspection import DatabaseInspector
from app.database.schema_manager import SchemaManager
from app.llm.base import LLMProvider
from app.llm.client import LLMClient
from app.llm.prompt_loader import PromptLoader
from app.models.entity import EntityRequirements
from app.observability import Observer
from app.pipeline.architecture_stage import ArchitectureStage
from app.pipeline.context import PipelineContext
from app.pipeline.crud_stage import CrudStage
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.schema_stage import SchemaStage
from app.pipeline.stage import StageServices
from app.pipeline.testing_stage import TestingStage


@dataclass
class Pipeline:
    orchestrator: PipelineOrchestrator
    context: PipelineContext
    connection: psycopg.Connection

    def close(self) -> None:
        self.connection.close()


def default_schema_name(settings: Settings, requirements: EntityRequirements) -> str:
    return (settings.target_schema or requirements.project).lower()


def build_pipeline(settings: Settings, provider: LLMProvider, observer: Observer,
                   requirements: EntityRequirements, connection: psycopg.Connection | None = None) -> Pipeline:
    schema = default_schema_name(settings, requirements)
    connection = connection or connect(settings, schema)
    manager = SchemaManager(connection, schema)
    artifacts = ArtifactStore(settings.generated_dir)
    llm = LLMClient(provider, PromptLoader(settings.prompts_dir), observer, settings.max_retries)
    services = StageServices(
        llm=llm, observer=observer, artifacts=artifacts, executor=DatabaseExecutor(connection),
        inspector=DatabaseInspector(connection, schema), schema_manager=manager,
        max_attempts=settings.max_retries, max_test_repair_rounds=settings.max_test_repair_rounds,
    )
    schema_stage, crud_stage = SchemaStage(services), CrudStage(services)
    stages = [
        ArchitectureStage(services), schema_stage, crud_stage,
        TestingStage(services, BddRunner(connection), schema_stage, crud_stage),
    ]
    context = PipelineContext(requirements, schema, pg_version=manager.server_version())
    return Pipeline(PipelineOrchestrator(stages, observer, artifacts), context, connection)
