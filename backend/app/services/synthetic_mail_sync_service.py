"""Offline synthetic read-only mailbox sync; no OAuth, HTTP, or real account support."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context
from .synthetic_mail_service import _address, _now, _text

_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Synthetic mailbox sync is available only to Jarvis")


def _id(value: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("Invalid synthetic identifier")
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid synthetic message timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Synthetic message timestamp needs a timezone")
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid synthetic message timestamp") from exc


@dataclass(frozen=True)
class FakeMailRecord:
    external_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: str

    def normalized(self) -> "FakeMailRecord":
        return FakeMailRecord(_id(self.external_id), _id(self.thread_id), _address(self.sender),
                              _text(self.subject, 500, "subject", required=True), _text(self.body, 100000, "body"),
                              _timestamp(self.received_at))


class FakeSyncTransientError(RuntimeError):
    pass


class FakeMailboxProvider:
    """Fixed in-memory fixture; intentionally has no network methods or credentials."""
    def __init__(self, dataset_id: str, records: list[FakeMailRecord], page_size: int = 20, *, fail_once_at: set[int] | None = None):
        self.dataset_id = _id(dataset_id)
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Synthetic page size must be between 1 and 100")
        if not isinstance(records, list) or len(records) > 5000 or any(type(item) is not FakeMailRecord for item in records):
            raise ValueError("Invalid synthetic fixture")
        self.records = tuple(item.normalized() for item in records)
        if sum(len(value.encode("utf-8")) for record in self.records for value in (record.external_id, record.thread_id, record.sender, record.subject, record.body, record.received_at)) > 5_000_000:
            raise ValueError("Synthetic fixture exceeds size limit")
        self.page_size = page_size
        self._fail_once_at = set(fail_once_at or ())
        self._failed = set()

    def fingerprint(self) -> str:
        """Detect accidental fixture changes during a synthetic run; not an authenticity check."""
        fields = [[r.external_id, r.thread_id, r.sender, r.subject, r.body, r.received_at] for r in self.records]
        encoded = json.dumps([self.dataset_id, self.page_size, fields], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def page(self, cursor: str, limit: int) -> tuple[tuple[FakeMailRecord, ...], str | None]:
        if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdigit():
            raise ValueError("Invalid synthetic cursor")
        offset = int(cursor)
        if offset > len(self.records) or type(limit) is not int or not 1 <= limit <= self.page_size:
            raise ValueError("Invalid synthetic page request")
        if offset in self._fail_once_at and offset not in self._failed:
            self._failed.add(offset)
            raise FakeSyncTransientError("Simulated temporary provider failure")
        end = min(offset + limit, len(self.records))
        return self.records[offset:end], (str(end) if end < len(self.records) else None)


class SyntheticMailSyncService:
    """User-started local fixture sync with per-page atomicity and no external adapter."""

    def create_connection(self, context: AssistantContext, dataset_id: str, account_address: str, *, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        dataset_id, account_address = _id(dataset_id), _address(account_address)
        connection_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("INSERT INTO synthetic_mail_connections(id,owner_id,dataset_id,account_address,status,created_at,updated_at) VALUES (?,?,?,?,'active',?,?)",
                         (connection_id, context.owner_id, dataset_id, account_address, now, now))
            self._audit(conn, context, "synthetic_connection.created", connection_id, now)
        return connection_id

    def start(self, context: AssistantContext, connection_id: str, provider: FakeMailboxProvider, max_items: int, *, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        if type(provider) is not FakeMailboxProvider or type(max_items) is not int or not 1 <= max_items <= 1000:
            raise ValueError("A local synthetic provider and a limit from 1 to 1000 are required")
        run_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = self._connection(conn, context.owner_id, connection_id)
            if connection["status"] != "active" or connection["dataset_id"] != provider.dataset_id:
                raise InvalidTransition("Synthetic connection is inactive or dataset does not match")
            conn.execute("INSERT INTO synthetic_sync_runs(id,owner_id,connection_id,status,next_cursor,max_items,page_size,processed_count,imported_count,updated_count,unchanged_count,created_at,updated_at,dataset_fingerprint) VALUES (?,?,?,'running','0',?,?,0,0,0,0,?,?,?)",
                         (run_id, context.owner_id, connection_id, max_items, provider.page_size, now, now, provider.fingerprint()))
            self._audit(conn, context, "synthetic_sync.started", run_id, now)
        return run_id

    def next_page(self, context: AssistantContext, run_id: str, provider: FakeMailboxProvider) -> dict:
        _jarvis(context)
        if type(provider) is not FakeMailboxProvider:
            raise ValueError("Only the in-process synthetic provider is available")
        with connect() as conn:
            run = self._run(conn, context.owner_id, run_id)
            connection = self._connection(conn, context.owner_id, run["connection_id"])
            if run["status"] != "running" or connection["status"] != "active":
                raise InvalidTransition("Synthetic sync is not active")
            if provider.dataset_id != connection["dataset_id"] or provider.page_size != run["page_size"] or provider.fingerprint() != run["dataset_fingerprint"]:
                raise InvalidTransition("Synthetic provider snapshot does not match the run")
            expected_cursor, expected_processed = run["next_cursor"], run["processed_count"]
            remaining = run["max_items"] - expected_processed
            if remaining <= 0 or expected_cursor is None:
                raise InvalidTransition("Synthetic sync has no remaining page")
        page, next_cursor = provider.page(expected_cursor, min(provider.page_size, remaining))
        unique = {}
        for record in page:
            previous = unique.get(record.external_id)
            if previous is not None and previous != record:
                raise ValueError("Conflicting synthetic records for one external ID")
            unique[record.external_id] = record
        now = _now()
        counts = {"imported": 0, "updated": 0, "unchanged": 0}
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self._run(conn, context.owner_id, run_id)
            connection = self._connection(conn, context.owner_id, run["connection_id"])
            if run["status"] != "running" or connection["status"] != "active" or run["next_cursor"] != expected_cursor or run["processed_count"] != expected_processed or provider.fingerprint() != run["dataset_fingerprint"]:
                raise InvalidTransition("Synthetic sync or fixture changed; reload its state")
            for record in unique.values():
                link = conn.execute("SELECT l.mail_id,l.thread_id,m.sender,m.subject,m.body,m.received_at FROM synthetic_mail_links l JOIN mail_items m ON m.id=l.mail_id AND m.owner_id=? WHERE l.connection_id=? AND l.external_id=?", (context.owner_id, run["connection_id"], record.external_id)).fetchone()
                if link is None:
                    mail_id = str(uuid4())
                    conn.execute("INSERT INTO mail_items(id,owner_id,source_kind,sender,subject,body,triage_status,priority,received_at,updated_at) VALUES (?,?,'synthetic',?,?,?,'new','normal',?,?)", (mail_id, context.owner_id, record.sender, record.subject, record.body, record.received_at, now))
                    conn.execute("INSERT INTO synthetic_mail_links(connection_id,external_id,thread_id,mail_id) VALUES (?,?,?,?)", (run["connection_id"], record.external_id, record.thread_id, mail_id))
                    counts["imported"] += 1
                else:
                    changed = (link["sender"], link["subject"], link["body"], link["received_at"]) != (record.sender, record.subject, record.body, record.received_at)
                    if changed:
                        conn.execute("UPDATE mail_items SET sender=?,subject=?,body=?,received_at=?,revision=revision+1,updated_at=? WHERE id=? AND owner_id=?", (record.sender, record.subject, record.body, record.received_at, now, link["mail_id"], context.owner_id))
                        conn.execute("UPDATE model_requests SET status='invalidated',payload_json='{}' WHERE owner_id=? AND source_type='email' AND source_id=? AND status='pending'", (context.owner_id, link["mail_id"]))
                        conn.execute("DELETE FROM ai_suggestions WHERE owner_id=? AND source_type='email' AND source_id=? AND status='proposed'", (context.owner_id, link["mail_id"]))
                        conn.execute("UPDATE action_proposals SET status='invalidated',payload_json='{}' WHERE owner_id=? AND kind='synthetic_email_send' AND status IN ('pending','approved') AND target_ref IN (SELECT id FROM mail_drafts WHERE owner_id=? AND mail_id=?)", (context.owner_id, context.owner_id, link["mail_id"]))
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1
                    if link["thread_id"] != record.thread_id:
                        conn.execute("UPDATE synthetic_mail_links SET thread_id=? WHERE connection_id=? AND external_id=?", (record.thread_id, run["connection_id"], record.external_id))
            processed = expected_processed + len(page)
            status = "completed" if next_cursor is None or processed >= run["max_items"] else "running"
            conn.execute("UPDATE synthetic_sync_runs SET status=?,next_cursor=?,processed_count=?,imported_count=imported_count+?,updated_count=updated_count+?,unchanged_count=unchanged_count+?,updated_at=? WHERE id=? AND owner_id=?", (status, next_cursor, processed, counts["imported"], counts["updated"], counts["unchanged"], now, run_id, context.owner_id))
            self._audit(conn, context, "synthetic_sync.page_committed", run_id, now)
        return self.get_run(context, run_id)

    def get_run(self, context: AssistantContext, run_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            row = dict(self._run(conn, context.owner_id, run_id))
            row.pop("owner_id", None)
            return row

    def revoke(self, context: AssistantContext, connection_id: str, *, confirmed_by_user: bool = False) -> None:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = self._connection(conn, context.owner_id, connection_id)
            if connection["status"] != "active":
                raise InvalidTransition("Synthetic connection is not active")
            conn.execute("UPDATE synthetic_mail_connections SET status='revoked',updated_at=? WHERE id=? AND owner_id=?", (now, connection_id, context.owner_id))
            conn.execute("UPDATE synthetic_sync_runs SET status='cancelled',updated_at=? WHERE connection_id=? AND owner_id=? AND status='running'", (now, connection_id, context.owner_id))
            self._audit(conn, context, "synthetic_connection.revoked", connection_id, now)

    @staticmethod
    def _connection(conn, owner_id: str, connection_id: str):
        row = conn.execute("SELECT * FROM synthetic_mail_connections WHERE id=? AND owner_id=?", (connection_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Synthetic connection not found")
        return row

    @staticmethod
    def _run(conn, owner_id: str, run_id: str):
        row = conn.execute("SELECT * FROM synthetic_sync_runs WHERE id=? AND owner_id=?", (run_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Synthetic sync not found")
        return row

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_id: str, now: str) -> None:
        conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,'synthetic_sync',?,'{}',?)", (str(uuid4()), context.owner_id, context.owner_id, event, object_id, now))
