"""Loads prompt templates from files. No prompt text lives in Python."""

import re
from pathlib import Path

from app.errors import PromptError

_INCLUDE = re.compile(r"\{\{include:([A-Za-z0-9_./-]+)\}\}")
_VARIABLE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
_MAX_INCLUDE_DEPTH = 3


class PromptLoader:
    """Prompt ids are paths without extension, e.g. 'schema/generate_schema'.

    Templates use {{VARIABLE}} placeholders and {{include:shared/file.md}} fragments.
    Rendering is strict: missing or unused variables are errors, so prompt/code drift is caught.
    """

    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir

    def template(self, prompt_id: str) -> str:
        return self._expand_includes(self._read(f"{prompt_id}.md"), depth=0)

    def required_variables(self, prompt_id: str) -> set[str]:
        return set(_VARIABLE.findall(self.template(prompt_id)))

    def render(self, prompt_id: str, variables: dict[str, str]) -> str:
        template = self.template(prompt_id)
        needed = set(_VARIABLE.findall(template))
        missing, unused = needed - variables.keys(), variables.keys() - needed
        if missing:
            raise PromptError(f"Prompt '{prompt_id}' needs variables {sorted(missing)}")
        if unused:
            raise PromptError(f"Prompt '{prompt_id}' does not use variables {sorted(unused)}")
        return _VARIABLE.sub(lambda m: str(variables[m.group(1)]), template)  # single pass

    def _read(self, relative: str) -> str:
        path = (self._dir / relative).resolve()
        if self._dir.resolve() not in path.parents:
            raise PromptError(f"Prompt path escapes prompts directory: {relative}")
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PromptError(f"Prompt file not found: {path}") from exc

    def _expand_includes(self, text: str, depth: int) -> str:
        if depth > _MAX_INCLUDE_DEPTH:
            raise PromptError("Prompt includes nested too deeply")
        return _INCLUDE.sub(lambda m: self._expand_includes(self._read(m.group(1)).strip(), depth + 1), text)
