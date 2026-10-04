"""Shared shape for LLM-generated SQL (schema and CRUD scripts)."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SqlScript(BaseModel):
    """An ordered list of complete SQL statements (no splitting by us needed)."""

    model_config = ConfigDict(extra="forbid")
    statements: list[str] = Field(min_length=1, description="One complete SQL statement per item")
    notes: list[str] = Field(default_factory=list, description="Design notes / assumptions")

    @field_validator("statements")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [s.strip() for s in value if s and s.strip()]
        if not cleaned:
            raise ValueError("statements must contain at least one non-empty statement")
        return cleaned

    def to_sql(self) -> str:
        return "\n\n".join(s if s.rstrip().endswith(";") else s + ";" for s in self.statements) + "\n"


class SchemaScript(SqlScript):
    """DDL for Stage 2."""
