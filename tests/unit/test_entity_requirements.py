import json

import pytest

from app.errors import RequirementsError
from app.models.entity import load_requirements


def write(tmp_path, data) -> object:
    path = tmp_path / "entities.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def entity(name="User", fields=None):
    return {"name": name, "fields": fields or [{"name": "id", "type": "integer"}]}


def test_example_file_is_valid():
    from pathlib import Path
    reqs = load_requirements(Path(__file__).resolve().parents[2] / "requirements" / "entities.json")
    assert {e.name for e in reqs.entities} >= {"User", "Product", "Order", "OrderItem"}


def test_missing_file_and_bad_json(tmp_path):
    with pytest.raises(RequirementsError, match="not found"):
        load_requirements(tmp_path / "nope.json")
    with pytest.raises(RequirementsError, match="not valid JSON"):
        load_requirements(write(tmp_path, "{oops"))


@pytest.mark.parametrize("data, message", [
    ({"project": "p", "entities": []}, "at least 1"),
    ({"project": "bad name", "entities": [entity()]}, "project"),
    ({"project": "p", "entities": [entity(), entity()]}, "duplicate entity"),
    ({"project": "p", "entities": [entity(fields=[{"name": "a", "type": "integer"}, {"name": "a", "type": "string"}])]}, "duplicate field"),
    ({"project": "p", "entities": [entity(fields=[{"name": "a", "type": "blob"}])]}, "type"),
    ({"project": "p", "entities": [entity(fields=[{"name": "a", "type": "integer", "references": {"entity": "Ghost", "field": "id"}}])]}, "unknown Ghost.id"),
    ({"project": "p", "entities": [entity()], "relationships": [{"from_entity": "User", "to_entity": "Ghost", "cardinality": "one_to_many"}]}, "unknown entity"),
    ({"project": "p", "entities": [entity()], "surprise": 1}, "surprise"),
])
def test_invalid_requirements_are_rejected(tmp_path, data, message):
    with pytest.raises(RequirementsError, match=message):
        load_requirements(write(tmp_path, data))
