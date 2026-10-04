"""PipelineContext: the single state object stages read from and write to."""

from dataclasses import asdict, dataclass, field

from app.models.architecture import Architecture
from app.models.crud import CrudScript
from app.models.entity import EntityRequirements
from app.models.schema import SchemaScript
from app.models.tests import BddSuite, TestReport


@dataclass
class AttemptRecord:
    stage: str
    attempt: int
    status: str  # "success" | "failed"
    error: str | None = None
    artifact: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineContext:
    requirements: EntityRequirements
    schema_name: str
    pg_version: str = "unknown"
    architecture: Architecture | None = None
    schema: SchemaScript | None = None
    crud: CrudScript | None = None
    tests: BddSuite | None = None
    test_report: TestReport | None = None
    errors: list[str] = field(default_factory=list)
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def project(self) -> str:
        return self.requirements.project

    def entity_json(self) -> str:
        return self.requirements.model_dump_json(indent=2, exclude_none=True)

    def require_architecture(self) -> Architecture:
        if self.architecture is None:
            raise RuntimeError("architecture stage has not completed")
        return self.architecture

    def require_schema(self) -> SchemaScript:
        if self.schema is None:
            raise RuntimeError("schema stage has not completed")
        return self.schema

    def require_crud(self) -> CrudScript:
        if self.crud is None:
            raise RuntimeError("CRUD stage has not completed")
        return self.crud
