import copy

import pytest

from app.database.dependency_graph import DependencyGraph
from app.database.identifiers import identifier_problems, normalize_type
from app.database.validator import ArchitectureValidator
from app.models.architecture import Architecture
from app.models.entity import EntityRequirements
from tests.fakes import shop_fixtures as shop

REQS = EntityRequirements.model_validate({
    "project": "shop",
    "entities": [{"name": n, "fields": [{"name": "id", "type": "integer"}]}
                 for n in ("User", "Category", "Product", "Order", "OrderItem")],
})


def build(mutate=None) -> Architecture:
    data = copy.deepcopy(shop.ARCHITECTURE)
    if mutate:
        mutate(data)
    return Architecture.model_validate(data)


def errors(mutate=None) -> list[str]:
    return ArchitectureValidator().validate(build(mutate), REQS).errors


def table(data, name):
    return next(t for t in data["tables"] if t["name"] == name)


def test_valid_architecture_has_no_errors():
    report = ArchitectureValidator().validate(build(), REQS)
    assert report.ok, report.format()


def test_unknown_referenced_table():
    errs = errors(lambda d: table(d, "orders")["foreign_keys"][0].update(ref_table="ghost"))
    assert any("'ghost' does not exist" in e for e in errs)


def test_unknown_referenced_column():
    errs = errors(lambda d: table(d, "orders")["foreign_keys"][0].update(ref_columns=["uid"]))
    assert any("uid" in e and "do not exist" in e for e in errs)


def test_incompatible_fk_types():
    errs = errors(lambda d: next(c for c in table(d, "orders")["columns"] if c["name"] == "user_id").update(type="text"))
    assert any("incompatible types" in e for e in errs)


def test_fk_must_target_key():
    def mutate(d):
        table(d, "orders")["foreign_keys"][0].update(ref_columns=["name"])
        next(c for c in table(d, "orders")["columns"] if c["name"] == "user_id").update(type="text")
    assert any("neither PRIMARY KEY nor UNIQUE" in e for e in errors(mutate))


def test_fk_may_target_unique_column():
    def mutate(d):
        table(d, "orders")["foreign_keys"][0].update(ref_columns=["email"])
        next(c for c in table(d, "orders")["columns"] if c["name"] == "user_id").update(type="text")
    assert errors(mutate) == []


def test_duplicate_table_and_column_names():
    assert any("duplicate table" in e for e in errors(lambda d: d["tables"].append(copy.deepcopy(d["tables"][0]))))
    assert any("duplicate column" in e for e in errors(lambda d: table(d, "users")["columns"].append({"name": "email", "type": "text"})))


def test_duplicate_foreign_key():
    errs = errors(lambda d: table(d, "orders")["foreign_keys"].append(copy.deepcopy(table(d, "orders")["foreign_keys"][0])))
    assert any("duplicate foreign key" in e for e in errs)


def test_identifier_rules():
    assert any("reserved" in e for e in errors(lambda d: table(d, "orders").update(name="order")))
    assert any("snake_case" in e for e in errors(lambda d: table(d, "users").update(name="Users")))
    assert any("63" in e for e in errors(lambda d: table(d, "users").update(name="a" * 64)))


def test_missing_primary_key_column():
    assert any("primary key column 'pk'" in e for e in errors(lambda d: table(d, "users")["primary_key"].update(columns=["pk"])))


def test_set_null_requires_nullable_columns():
    errs = errors(lambda d: table(d, "orders")["foreign_keys"][0].update(on_delete="SET NULL"))
    assert any("SET NULL requires nullable" in e for e in errs)


def test_unresolved_relationship_and_junction():
    def drop_fk(d):
        table(d, "order_items")["foreign_keys"] = [fk for fk in table(d, "order_items")["foreign_keys"] if fk["ref_table"] != "orders"]
    assert any("unresolved relationship" in e for e in errors(drop_fk))
    assert any("via_table" in e for e in errors(lambda d: d["relationships"][3].update(via_table=None)))


def test_every_entity_must_be_covered():
    assert any("'Order' is not modelled" in e for e in errors(lambda d: table(d, "orders").update(source_entities=[])))


def test_first_normal_form_violations():
    assert any("array type" in e for e in errors(lambda d: table(d, "users")["columns"].append({"name": "tags", "type": "text[]"})))
    def repeating(d):
        table(d, "users")["columns"] += [{"name": "phone1", "type": "text"}, {"name": "phone2", "type": "text"}]
    assert any("repeating group" in e for e in errors(repeating))


def test_justified_denormalization_downgrades_to_warning():
    def mutate(d):
        table(d, "users")["columns"].append({"name": "tags", "type": "text[]"})
        d["normalization"]["intentional_denormalizations"].append({"table": "users", "reason": "read-mostly tag list"})
    report = ArchitectureValidator().validate(build(mutate), REQS)
    assert report.ok and any("array type" in w for w in report.warnings)


def test_unexplained_normalization_violation_is_error():
    assert any("violations" in e for e in errors(lambda d: d["normalization"]["violations"].append("orders.user_name is transitive")))


def test_transitive_copy_is_warned():
    def mutate(d):
        table(d, "orders")["columns"].append({"name": "user_email", "type": "text"})
    report = ArchitectureValidator().validate(build(mutate), REQS)
    assert any("3NF" in w for w in report.warnings)


# --- dependency graph ---------------------------------------------------------------------------
def cyclic(d):
    table(d, "users")["columns"].append({"name": "last_order_id", "type": "integer", "nullable": True})
    table(d, "users")["foreign_keys"].append({"columns": ["last_order_id"], "ref_table": "orders", "ref_columns": ["id"],
                                              "on_delete": "SET NULL"})


def test_topological_order_respects_dependencies():
    order = DependencyGraph.from_architecture(build()).topological_order()
    assert order.index("users") < order.index("orders") < order.index("order_items")
    assert order.index("products") < order.index("order_items")


def test_cycle_detected_and_reported():
    arch = build(cyclic)
    graph = DependencyGraph.from_architecture(arch)
    assert graph.find_cycles() == [["orders", "users"]]
    with pytest.raises(ValueError, match="cycle"):
        graph.topological_order()
    assert any("deferred" in e for e in ArchitectureValidator().validate(arch, REQS).errors)


def test_deferred_foreign_key_breaks_cycle():
    def mutate(d):
        cyclic(d)
        table(d, "users")["foreign_keys"][0]["deferred"] = True
    arch = build(mutate)
    assert DependencyGraph.from_architecture(arch).find_cycles() == []
    assert ArchitectureValidator().validate(arch, REQS).ok
    assert DependencyGraph.from_architecture(arch, include_deferred=True).find_cycles()


def test_self_reference_is_not_a_cycle():
    def mutate(d):
        table(d, "categories")["columns"].append({"name": "parent_id", "type": "integer", "nullable": True})
        table(d, "categories")["foreign_keys"].append({"columns": ["parent_id"], "ref_table": "categories", "ref_columns": ["id"]})
    assert ArchitectureValidator().validate(build(mutate), REQS).ok


def test_wrong_creation_order_is_only_a_warning():
    report = ArchitectureValidator().validate(build(lambda d: d.update(creation_order=list(reversed(d["creation_order"])))), REQS)
    assert report.ok and any("recomputed" in w for w in report.warnings)
    assert any("missing tables" in e for e in errors(lambda d: d["creation_order"].pop()))


def test_identifier_helpers():
    assert identifier_problems("order_items") == []
    assert identifier_problems("user")
    assert normalize_type("VARCHAR(255)") == normalize_type("text") == "text"
    assert normalize_type("serial") == normalize_type("int4") == "integer"
    assert normalize_type("numeric(12,2)") == normalize_type("decimal")
