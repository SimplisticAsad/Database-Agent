"""A scripted LLMProvider for tests: replies are queued per prompt-id (read from the prompt header)."""

import json
import re
from collections import defaultdict, deque

from app.llm.base import LLMProvider

_PROMPT_ID = re.compile(r"<!-- prompt-id: ([\w.]+) -->")


class ScriptedLLM(LLMProvider):
    def __init__(self, script: dict[str, list]) -> None:
        self._queues = {pid: deque(replies) for pid, replies in script.items()}
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = defaultdict(list)

    def generate(self, prompt: str) -> str:
        match = _PROMPT_ID.search(prompt)
        assert match, "prompt has no prompt-id header"
        prompt_id = match.group(1)
        self.calls.append(prompt_id)
        self.prompts[prompt_id].append(prompt)
        queue = self._queues.get(prompt_id)
        assert queue, f"unexpected LLM call for {prompt_id}; calls so far: {self.calls}"
        reply = queue.popleft()
        return reply if isinstance(reply, str) else json.dumps(reply)
