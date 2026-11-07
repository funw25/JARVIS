"""Input-model and development-token checks; this is not an HTTP test."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from pydantic import ValidationError
from app.api.v1.schemas import ConversationCreate, MemoryCandidateCreate, SourceRef, TodoCreate
from app.security.local_token import matches_development_token, valid_development_token


def rejected(factory, **values):
    try:
        factory(**values)
    except ValidationError:
        return
    raise AssertionError("invalid fields unexpectedly accepted")


assert TodoCreate(title="synthetic").title == "synthetic"
rejected(TodoCreate, title="synthetic", owner_id="other-owner")
rejected(ConversationCreate, title="synthetic", assistant_id="other")
rejected(SourceRef, type="message", id="synthetic", owner_id="other-owner")
rejected(MemoryCandidateCreate, category="test", content="synthetic", unexpected="value")
rejected(TodoCreate, title="\ud800")
rejected(TodoCreate, title="valid", sources=[{"type":"email", "id":"\ud800"}])
rejected(ConversationCreate, **{"\ud800":"value"})
print("API_INPUT_EXTRA_FIELDS_REJECTED=PASS")

strong = "A" * 43
assert valid_development_token(strong) and matches_development_token(strong, strong)
for invalid in (None, "short", "x" * 129, "你好" * 30, " " + strong, strong + " "):
    assert not valid_development_token(invalid)
    assert not matches_development_token(strong, invalid)
assert not matches_development_token(strong, "B" * 43)
print("LOCAL_TOKEN_FORMAT_AND_COMPARISON=PASS")
print("HTTP_RUNTIME=NOT_TESTED")
