"""Deterministic validation of the LLM-proposed architecture (Stage 1)."""

import re
from collections import Counter
from dataclasses import dataclass, field

from app.database.dependency_graph import DependencyGraph
from app.database.identifiers import identifier_problems, is_array_type, normalize_type
from app.models.architecture import (
    Architecture,
    ReferentialAction,
    RelationshipCardinality,
    TableSpec,
)
from app.models.entity import EntityRequirements

_REPEATING_GROUP_RE = re.compile(r"^(.*?)_?(\d+)$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        lines = [f"ERROR: {e}" for e in self.errors] + [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "no issues"


class ArchitectureValidator:
    """Runs every check and returns all problems at once (best for LLM correction)."""

    def validate(self, architecture: Architecture, requirements: EntityRequirements) -> ValidationReport:
        report = ValidationReport()
        tables = {t.name: t for t in architecture.tables}
        self._check_names(architecture, report)
        if report.errors:  # later checks assume unique, valid names
            return report
        for table in architecture.tables:
            self._check_table(table, report)
        for table in architecture.tables:
            self._check_foreign_keys(table, tables, report)
        self._check_relationships(architecture, tables, report)
        self._check_dependency_graph(architecture, report)
        self._check_normalization(architecture, report)
        self._check_entity_coverage(architecture, requirements, report)
        return report

    # -- names -------------------------------------------------------------
    def _check_names(self, arch: Architecture, report: ValidationReport) -> None:
        for name, count in Counter(t.name for t in arch.tables).items():
            if count > 1:
                report.errors.append(f"duplicate table name '{name}'")
        for table in arch.tables:
            for problem in identifier_problems(table.name):
                report.errors.append(f"table {problem}")
            for column in table.columns:
                for problem in identifier_problems(column.name):
                    report.errors.append(f"{table.name}: column {problem}")
            for column, count in Counter(c.name for c in table.columns).items():
                if count > 1:
                    report.errors.append(f"{table.name}: duplicate column name '{column}'")

    # -- per table -----------------------------------------------------------
    def _check_table(self, table: TableSpec, report: ValidationReport) -> None:
        names = {c.name for c in table.columns}
        for col in table.primary_key.columns:
            if col not in names:
                report.errors.append(f"{table.name}: primary key column '{col}' does not exist")
        for cols in table.unique_constraints:
            self._require_columns(table, cols, "unique constraint", names, report)
        for idx in table.indexes:
            self._require_columns(table, idx.columns, "index", names, report)
            if set(idx.columns) == set(table.primary_key.columns):
                report.warnings.append(f"{table.name}: index on {idx.columns} duplicates the primary key")
        seen_fk: set[tuple] = set()
        for fk in table.foreign_keys:
            self._require_columns(table, fk.columns, "foreign key", names, report)
            key = (tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns))
            if key in seen_fk:
                report.errors.append(f"{table.name}: duplicate foreign key {fk.columns} -> {fk.ref_table}")
            seen_fk.add(key)

    @staticmethod
    def _require_columns(table, cols, label, names, report) -> None:
        for col in cols:
            if col not in names:
                report.errors.append(f"{table.name}: {label} references unknown column '{col}'")

    # -- foreign keys ----------------------------------------------------------
    def _check_foreign_keys(self, table: TableSpec, tables: dict[str, TableSpec], report: ValidationReport) -> None:
        for fk in table.foreign_keys:
            parent = tables.get(fk.ref_table)
            label = f"{table.name}.{','.join(fk.columns)} -> {fk.ref_table}.{','.join(fk.ref_columns)}"
            if parent is None:
                report.errors.append(f"{label}: referenced table '{fk.ref_table}' does not exist")
                continue
            if len(fk.columns) != len(fk.ref_columns):
                report.errors.append(f"{label}: column count mismatch")
                continue
            parent_cols = {c.name: c for c in parent.columns}
            missing = [c for c in fk.ref_columns if c not in parent_cols]
            if missing:
                report.errors.append(f"{label}: referenced column(s) {missing} do not exist")
                continue
            self._check_fk_types(table, fk, parent_cols, label, report)
            if not self._is_key(parent, fk.ref_columns):
                report.errors.append(f"{label}: referenced columns are neither PRIMARY KEY nor UNIQUE")
            self._check_actions(table, fk, label, report)

    @staticmethod
    def _check_fk_types(table, fk, parent_cols, label, report) -> None:
        own = {c.name: c for c in table.columns}
        for child, parent in zip(fk.columns, fk.ref_columns):
            if child not in own:
                continue  # already reported by _check_table
            if normalize_type(own[child].type) != normalize_type(parent_cols[parent].type):
                report.errors.append(
                    f"{label}: incompatible types {own[child].type} vs {parent_cols[parent].type}"
                )

    @staticmethod
    def _is_key(parent: TableSpec, columns: list[str]) -> bool:
        wanted = set(columns)
        keys = [parent.primary_key.columns, *parent.unique_constraints]
        keys += [i.columns for i in parent.indexes if i.unique]
        return any(set(k) == wanted for k in keys)

    @staticmethod
    def _check_actions(table, fk, label, report) -> None:
        nullable = {c.name: c.nullable and c.name not in table.primary_key.columns for c in table.columns}
        for action, name in ((fk.on_delete, "ON DELETE"), (fk.on_update, "ON UPDATE")):
            if action is ReferentialAction.SET_NULL and not all(nullable.get(c, False) for c in fk.columns):
                report.errors.append(f"{label}: {name} SET NULL requires nullable foreign key columns")

    # -- relationships -------------------------------------------------------
    def _check_relationships(self, arch: Architecture, tables: dict[str, TableSpec], report: ValidationReport) -> None:
        for rel in arch.relationships:
            label = f"relationship {rel.parent_table}/{rel.child_table}"
            if rel.parent_table not in tables or rel.child_table not in tables:
                report.errors.append(f"{label}: refers to an unknown table")
                continue
            if rel.cardinality is RelationshipCardinality.MANY_TO_MANY:
                self._check_many_to_many(rel, tables, label, report)
            elif not self._has_fk(tables[rel.child_table], rel.parent_table):
                report.errors.append(
                    f"{label}: no foreign key from {rel.child_table} to {rel.parent_table} (unresolved relationship)"
                )

    def _check_many_to_many(self, rel, tables, label, report) -> None:
        via = tables.get(rel.via_table or "")
        if via is None:
            report.errors.append(f"{label}: many_to_many needs an existing via_table (junction)")
        elif not (self._has_fk(via, rel.parent_table) and self._has_fk(via, rel.child_table)):
            report.errors.append(f"{label}: junction '{via.name}' must reference both tables")

    @staticmethod
    def _has_fk(table: TableSpec, target: str) -> bool:
        return any(fk.ref_table == target for fk in table.foreign_keys)

    # -- dependency graph ----------------------------------------------------
    def _check_dependency_graph(self, arch: Architecture, report: ValidationReport) -> None:
        graph = DependencyGraph.from_architecture(arch)
        cycles = graph.find_cycles()
        if cycles:
            report.errors.append(
                f"foreign keys form dependency cycle(s) {cycles}; mark at least one foreign key per cycle "
                "as deferred=true (it is added via ALTER TABLE after creation) and make its columns nullable"
            )
            return
        names = {t.name for t in arch.tables}
        unknown = [n for n in arch.creation_order if n not in names]
        if unknown:
            report.errors.append(f"creation_order contains unknown tables {unknown}")
        missing = names - set(arch.creation_order)
        if missing:
            report.errors.append(f"creation_order is missing tables {sorted(missing)}")
        if not unknown and not missing:
            position = {n: i for i, n in enumerate(arch.creation_order)}
            for table, deps in graph.edges.items():
                if any(position[d] > position[table] for d in deps):
                    report.warnings.append("creation_order violates dependencies; it will be recomputed")
                    break

    # -- normalization -------------------------------------------------------
    def _check_normalization(self, arch: Architecture, report: ValidationReport) -> None:
        justified = {d.table for d in arch.normalization.intentional_denormalizations}
        if arch.normalization.violations and not arch.normalization.intentional_denormalizations:
            report.errors.append(
                "normalization.violations is non-empty but no intentional_denormalizations explain them; "
                "fix the design or justify it"
            )
        tables = {t.name: t for t in arch.tables}
        for table in arch.tables:
            self._check_first_normal_form(table, table.name in justified, report)
            self._check_transitive_copies(table, tables, report)

    @staticmethod
    def _check_first_normal_form(table: TableSpec, justified: bool, report: ValidationReport) -> None:
        sink = report.warnings if justified else report.errors
        for col in table.columns:
            if is_array_type(col.type):
                sink.append(f"{table.name}.{col.name}: array type violates 1NF; model it as a related table")
        stems = Counter(m.group(1) for c in table.columns if (m := _REPEATING_GROUP_RE.match(c.name)) and m.group(1))
        for stem, count in stems.items():
            if count > 1:
                sink.append(f"{table.name}: numbered columns '{stem}*' look like a repeating group (1NF)")

    @staticmethod
    def _check_transitive_copies(table: TableSpec, tables: dict[str, TableSpec], report: ValidationReport) -> None:
        own = {c.name for c in table.columns}
        for fk in table.foreign_keys:
            parent = tables.get(fk.ref_table)
            if parent is None or len(fk.columns) != 1 or not fk.columns[0].endswith("_id"):
                continue
            prefix = fk.columns[0][: -len("_id")]
            for col in parent.columns:
                copy = f"{prefix}_{col.name}"
                if col.name not in parent.primary_key.columns and copy in own:
                    report.warnings.append(
                        f"{table.name}.{copy} may duplicate {parent.name}.{col.name} (possible 3NF transitive dependency)"
                    )

    # -- entity coverage -----------------------------------------------------
    def _check_entity_coverage(self, arch: Architecture, reqs: EntityRequirements, report: ValidationReport) -> None:
        covered = {e.lower() for t in arch.tables for e in t.source_entities}
        for entity in reqs.entities:
            if entity.name.lower() not in covered:
                report.errors.append(f"entity '{entity.name}' is not modelled by any table (source_entities)")
