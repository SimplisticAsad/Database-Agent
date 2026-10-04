"""Domain exceptions. Each carries enough context to be shown to a human."""


class DatabaseAgentError(Exception):
    """Base class for every error raised deliberately by the agent."""


class RequirementsError(DatabaseAgentError):
    """The entity JSON is missing, malformed, or semantically invalid."""


class PromptError(DatabaseAgentError):
    """A prompt template is missing or its variables do not match."""


class LLMError(DatabaseAgentError):
    """The LLM provider failed or returned something unusable."""


class StructuredOutputError(LLMError):
    """The LLM never produced output matching the expected Pydantic model."""


class UnsafeSQLError(DatabaseAgentError):
    """Generated SQL was rejected by the safety validator."""


class SchemaGuardError(DatabaseAgentError):
    """The target PostgreSQL schema is not safe for the agent to manage."""


class StageFailedError(DatabaseAgentError):
    """A pipeline stage exhausted its attempts."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"Stage '{stage}' failed: {message}")
        self.stage = stage
        self.message = message
