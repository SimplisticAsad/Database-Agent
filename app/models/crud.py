"""CRUD stage output contract."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.schema import SqlScript


class CrudOperation(str, Enum):
    CREATE = "create"
    GET = "get"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    CUSTOM = "custom"


class CrudFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Function/procedure name exactly as created")
    table: str | None = None
    operation: CrudOperation
    description: str = ""


class SkippedTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    reason: str


class CrudScript(SqlScript):
    functions: list[CrudFunction] = Field(min_length=1)
    skipped_tables: list[SkippedTable] = Field(default_factory=list)
