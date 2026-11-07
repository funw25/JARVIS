import os
from fastapi import Header, HTTPException, status

from .assistant_context import AssistantContext, validate_context
from .local_token import matches_development_token, valid_development_token

def configured_token() -> str | None:
    token = os.environ.get("JARVIS_LOCAL_API_TOKEN", "")
    return token if valid_development_token(token) else None

def current_owner() -> str:
    return os.environ.get("JARVIS_OWNER_ID", "local-owner").strip() or "local-owner"

def authenticated_owner(authorization: str | None = Header(default=None)) -> str:
    expected = configured_token()
    if expected is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Local API is not configured")
    scheme, separator, supplied = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if not matches_development_token(expected, supplied.strip()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid local token", headers={"WWW-Authenticate": "Bearer"})
    owner = current_owner()
    if not owner:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Local owner is not configured")
    return owner

def assistant_context(owner_id: str, assistant_id: str = Header(alias="X-Jarvis-Assistant")) -> AssistantContext:
    if assistant_id not in ("companion", "jarvis"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown assistant")
    context = AssistantContext(owner_id=owner_id, assistant_id=assistant_id)  # type: ignore[arg-type]
    validate_context(context)
    return context
