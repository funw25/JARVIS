from pydantic import BaseModel, ConfigDict, Field, model_validator

class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_invalid_unicode(cls, value):
        stack = [(value, 0)]
        count = 0
        while stack:
            item, depth = stack.pop()
            count += 1
            if count > 100000 or depth > 32:
                raise ValueError("Input structure exceeds limits")
            if isinstance(item, str):
                try:
                    item.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise ValueError("Input contains invalid Unicode") from exc
            elif isinstance(item, dict):
                if len(stack) + 2 * len(item) > 100000:
                    raise ValueError("Input structure exceeds limits")
                for key, child in item.items():
                    stack.append((key, depth + 1))
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                if len(stack) + len(item) > 100000:
                    raise ValueError("Input structure exceeds limits")
                stack.extend((child, depth + 1) for child in item)
        return value

class SourceRef(ApiModel):
    type: str
    id: str
    excerpt: str | None = None
    relation: str = "derived_from"

class ConversationCreate(ApiModel):
    title: str = Field(default="", max_length=300)

class MessageCreate(ApiModel):
    role: str = Field(pattern="^(user|assistant)$")
    body: str = Field(min_length=1, max_length=50000)
    source_refs: list[dict] = Field(default_factory=list)

class MemoryCandidateCreate(ApiModel):
    category: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=10000)
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)

class MemoryShareCreate(ApiModel):
    target_assistant: str
    memory_id: str | None = None
    namespace_id: str | None = None

class ContinuityDraftCreate(ApiModel):
    conversation_id: str
    summary: str = Field(min_length=1, max_length=10000)
    topics: list[str] = Field(default_factory=list, max_length=100)
    open_items: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[dict] = Field(default_factory=list)

class ContinuityAccept(ApiModel):
    title: str = Field(default="续聊", max_length=300)

class TodoCreate(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10000)
    priority: str = "normal"
    due_at: str | None = None
    sources: list[SourceRef] = Field(default_factory=list, max_length=100)

class TodoUpdate(ApiModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    priority: str | None = None
    due_at: str | None = None
    clear_due_at: bool = False

class TodoStatusChange(ApiModel):
    status: str

class TodoShareCreate(ApiModel):
    target_assistant: str


class SyntheticMailCreate(ApiModel):
    sender: str = Field(min_length=1, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100000)

class SyntheticMailTriage(ApiModel):
    triage_status: str
    priority: str

class SyntheticDraftWrite(ApiModel):
    account: str = Field(min_length=1, max_length=320)
    recipients: list[str] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100000)

class SyntheticProposalApproval(ApiModel):
    confirmed_hash: str = Field(pattern="^[0-9a-f]{64}$")

class SyntheticSourceTodo(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10000)
    priority: str = "normal"
    due_at: str | None = None


class SyntheticCaptureCreate(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=100000)
    source_url: str | None = Field(default=None, max_length=2048)

class SyntheticCaptureStatus(ApiModel):
    status: str

class SyntheticNoteCreate(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100000)
    source_capture_id: str | None = Field(default=None, max_length=128)

class SyntheticNoteUpdate(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100000)


class SyntheticModelRequestCreate(ApiModel):
    purpose: str = Field(min_length=1, max_length=100)
    source_id: str = Field(min_length=1, max_length=128)


class SyntheticReminderCreate(ApiModel):
    todo_id: str = Field(min_length=1, max_length=128)
    offset_minutes: int = Field(ge=0, le=10080, strict=True)


class SyntheticReminderEnabled(ApiModel):
    enabled: bool = Field(strict=True)


class SyntheticReminderAck(ApiModel):
    token: str = Field(pattern="^[0-9a-f]{64}$")


class SyntheticMailRuleCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    sender_exact: str | None = Field(default=None, max_length=320)
    subject_contains: str | None = Field(default=None, max_length=200)
    target_triage: str | None = None
    target_priority: str | None = None


class SyntheticRuleApproval(ApiModel):
    confirmed_hash: str = Field(pattern="^[0-9a-f]{64}$")


class SyntheticKnowledgeTagWrite(ApiModel):
    tag: str = Field(min_length=1, max_length=64)
