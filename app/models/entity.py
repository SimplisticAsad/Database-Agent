"""Entity JSON contract: the only high-level input of the Database Agent."""

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.errors import RequirementsError


class FieldType(str, Enum):
    INTEGER = "integer"
    STRING = "string"
    TEXT = "text"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    DATE = "date"
    UUID = "uuid"


class Cardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class FieldReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    field: str


class EntityField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    type: FieldType
    description: str = ""
    required: bool | None = None
    unique: bool = False
    references: FieldReference | None = None


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str = ""
    fields: list[EntityField] = Field(min_length=1)


class EntityRelationship(BaseModel):
    """Optional high-level relationship hint ('parent' side first)."""

    model_config = ConfigDict(extra="forbid")
    from_entity: str
    to_entity: str
    cardinality: Cardinality
    description: str = ""


class EntityRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    entities: list[Entity] = Field(min_length=1)
    relationships: list[EntityRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_semantics(self) -> "EntityRequirements":
        names = [e.name for e in self.entities]
        _ensure_unique(names, "entity name")
        fields_by_entity = {e.name: {f.name for f in e.fields} for e in self.entities}
        for entity in self.entities:
            _ensure_unique([f.name for f in entity.fields], f"field in {entity.name}")
            for f in entity.fields:
                _check_reference(entity.name, f, fields_by_entity)
        for rel in self.relationships:
            for end in (rel.from_entity, rel.to_entity):
                if end not in fields_by_entity:
                    raise ValueError(f"relationship refers to unknown entity '{end}'")
        return self


def _ensure_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value.lower() in seen:
            raise ValueError(f"duplicate {label}: '{value}'")
        seen.add(value.lower())


def _check_reference(entity: str, field: EntityField, known: dict[str, set[str]]) -> None:
    ref = field.references
    if ref is None:
        return
    if ref.entity not in known or ref.field not in known[ref.entity]:
        raise ValueError(
            f"{entity}.{field.name} references unknown {ref.entity}.{ref.field}"
        )


def load_requirements(path: Path) -> EntityRequirements:
    """Read and validate the entity JSON; raises RequirementsError with details."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RequirementsError(f"Requirements file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RequirementsError(f"Requirements file is not valid JSON: {exc}") from exc
    try:
        return EntityRequirements.model_validate(raw)
    except ValidationError as exc:
        raise RequirementsError(f"Invalid requirements:\n{exc}") from exc
