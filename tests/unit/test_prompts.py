import re
from pathlib import Path

import pytest

from app.errors import PromptError
from app.llm.prompt_loader import PromptLoader

PROMPTS = Path(__file__).resolve().parents[2] / "app" / "prompts"
SECTIONS = ["ROLE", "OBJECTIVE", "INPUT", "CONTEXT", "CONSTRAINTS", "DATABASE RULES", "REASONING CONSIDERATIONS",
            "OUTPUT FORMAT", "VALIDATION REQUIREMENTS", "FAILURE AND CORRECTION EXPECTATIONS"]
ALL_PROMPTS = sorted(str(p.relative_to(PROMPTS).with_suffix("")) for p in PROMPTS.rglob("*.md") if "shared" not in p.parts)


def make(tmp_path, files: dict[str, str]) -> PromptLoader:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    return PromptLoader(tmp_path)


def test_render_substitutes_and_includes(tmp_path):
    loader = make(tmp_path, {"a.md": "Hi {{NAME}}!\n{{include:shared/x.md}}", "shared/x.md": "shared {{NAME}}"})
    assert loader.render("a", {"NAME": "Bob"}) == "Hi Bob!\nshared Bob"


def test_values_are_not_re_expanded(tmp_path):
    loader = make(tmp_path, {"a.md": "{{A}} {{B}}"})
    assert loader.render("a", {"A": "{{B}}", "B": "x"}) == "{{B}} x"


def test_strict_variables(tmp_path):
    loader = make(tmp_path, {"a.md": "{{A}}"})
    with pytest.raises(PromptError, match="needs variables"):
        loader.render("a", {})
    with pytest.raises(PromptError, match="does not use"):
        loader.render("a", {"A": "1", "Z": "2"})
    with pytest.raises(PromptError, match="not found"):
        loader.render("missing", {})


def test_json_braces_and_lowercase_placeholders_survive(tmp_path):
    loader = make(tmp_path, {"a.md": 'schema {"a": {"b": 1}} and {{user_id}} {{A}}'})
    assert loader.render("a", {"A": "x"}) == 'schema {"a": {"b": 1}} and {{user_id}} x'


def test_include_cannot_escape_prompts_dir(tmp_path):
    loader = make(tmp_path, {"a.md": "{{include:../secret.md}}"})
    with pytest.raises(PromptError):
        loader.render("a", {})


@pytest.mark.parametrize("prompt_id", ALL_PROMPTS)
def test_every_prompt_is_dimensional(prompt_id):
    text = PromptLoader(PROMPTS).template(prompt_id)
    assert re.match(r"<!-- prompt-id: [\w.]+ -->", text)
    for section in SECTIONS:
        assert re.search(rf"^# .*{re.escape(section)}", text, re.M), f"{prompt_id} lacks section {section}"
    assert "{{OUTPUT_JSON_SCHEMA}}" in text


def test_all_expected_prompt_files_exist():
    assert ALL_PROMPTS == [
        "architecture/generate_architecture", "correction/architecture_correction", "correction/crud_correction",
        "correction/schema_correction", "correction/structured_output_correction", "correction/test_correction",
        "correction/test_failure_triage", "crud/generate_crud", "schema/generate_schema", "testing/generate_bdd_tests",
    ]
    ids = [re.search(r"prompt-id: ([\w.]+)", (PROMPTS / f"{p}.md").read_text()).group(1) for p in ALL_PROMPTS]
    assert len(set(ids)) == len(ids)
