from dataclasses import dataclass
from typing import Literal

AssistantId = Literal["companion", "jarvis"]

@dataclass(frozen=True)
class AssistantContext:
    owner_id: str
    assistant_id: AssistantId

class AccessDenied(PermissionError):
    """The requested data is outside this assistant's approved scope."""

class NotFound(LookupError):
    """The object does not exist in the current owner's scope."""

class InvalidTransition(ValueError):
    """The requested state transition is not allowed."""

def validate_context(context: AssistantContext) -> None:
    if not context.owner_id.strip():
        raise AccessDenied("A valid owner context is required")
    if context.assistant_id not in ("companion", "jarvis"):
        raise AccessDenied("Unknown assistant identity")
