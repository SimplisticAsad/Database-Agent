"""Architecture JSON contract produced by Stage 1 (LLM) and checked by code."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ReferentialAction(str, Enum):
    NO_ACTION = "NO ACTION"
    RESTRICT = "RESTRICT"
    CASCADE = "CASCADE"
    SET_NULL = "SET NULL"
    SET_DEFAULT = "SET DEFAULT"


class RelationshipCardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnSpec(_Strict):
    name: str = Field(description="snake_case column name")
    type: str = Field(description="PostgreSQL type, e.g. integer, text, numeric(12,2), timestamptz")
    nullable: bool = Field(default=True, description="False => NOT NULL")
    source_field: str | None = Field(
        default=None, description="Entity JSON field this column came from, if any"
    )


class PrimaryKeySpec(_Strict):
    columns: list[str] = Field(min_length=1)


class ForeignKeySpec(_Strict):
    columns: list[str] = Field(min_length=1)
    ref_table: str
    ref_columns: list[str] = Field(min_length=1)
    on_delete: ReferentialAction = ReferentialAction.RESTRICT
    on_update: ReferentialAction = ReferentialAction.NO_ACTION
    deferred: bool = Field(
        default=False,
        description="True => added with ALTER TABLE after all tables exist (used to break dependency cycles)",
    )
    rationale: str = ""


class IndexSpec(_Strict):
    columns: list[str] = Field(min_length=1)
    unique: bool = False
    reason: str = Field(description="Access pattern or relationship justifying the index")


class TableSpec(_Strict):
    name: str
    purpose: str
    source_entities: list[str] = Field(
        default_factory=list, description="Entity names modelled by this table (empty for junction tables)"
    )
    columns: list[ColumnSpec] = Field(min_length=1)
    primary_key: PrimaryKeySpec
    foreign_keys: list[ForeignKeySpec] = Field(default_factory=list)
    unique_constraints: list[list[str]] = Field(default_factory=list)
    indexes: list[IndexSpec] = Field(default_factory=list)
    dependencies: list[str] = Field(
        default_factory=list, description="Tables that must exist before this one"
    )


class RelationshipSpec(_Strict):
    parent_table: str = Field(description="Referenced ('one') side")
    child_table: str = Field(description="Referencing ('many') side; for many_to_many the first table")
    cardinality: RelationshipCardinality
    via_table: str | None = Field(default=None, description="Junction table for many_to_many")
    description: str = ""


class IntentionalDenormalization(_Strict):
    table: str
    reason: str = Field(min_length=1)


class NormalizationAnalysis(_Strict):
    target: str = "3NF"
    analysis: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    intentional_denormalizations: list[IntentionalDenormalization] = Field(default_factory=list)


class Architecture(_Strict):
    tables: list[TableSpec] = Field(min_length=1)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    creation_order: list[str] = Field(default_factory=list)
    normalization: NormalizationAnalysis = Field(default_factory=NormalizationAnalysis)
    normalization_notes: list[str] = Field(default_factory=list)

    def table(self, name: str) -> TableSpec | None:
        return next((t for t in self.tables if t.name == name), None)
