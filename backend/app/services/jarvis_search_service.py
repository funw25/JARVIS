"""Bounded keyword search across synthetic Jarvis-owned local resources."""
from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context

# SQL identifiers and conditions are fixed constants; user text is always a parameter.
_SOURCES = {
    "mail": ("mail_items", "subject", "body", "source_kind='synthetic'"),
    "todo": ("todos", "title", "description", "created_by_assistant='jarvis' AND deleted_at IS NULL"),
    "capture": ("capture_items", "title", "content", "source_kind='manual_synthetic' AND deleted_at IS NULL"),
    "note": ("knowledge_items", "title", "body", "deleted_at IS NULL"),
}


class JarvisSearchService:
    def search(self, context: AssistantContext, query: str, *, kinds: tuple[str, ...] = ("mail", "todo", "capture", "note"), limit: int = 25) -> dict:
        validate_context(context)
        if context.assistant_id != "jarvis":
            raise AccessDenied("Unified search is available only to Jarvis")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("A query of 1 to 200 characters is required")
        query = query.strip()
        if not isinstance(kinds, (list, tuple)) or not kinds or len(kinds) > len(_SOURCES) or any(not isinstance(kind, str) or kind not in _SOURCES for kind in kinds) or len(set(kinds)) != len(kinds):
            raise ValueError("Invalid search kinds")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        selects, params = [], []
        for kind in sorted(kinds):
            table, title, content, condition = _SOURCES[kind]
            selects.append(
                f"SELECT '{kind}' AS kind,id,{title} AS title,updated_at, "
                f"CASE WHEN instr(lower({title}),lower(?))>0 THEN 'title' ELSE 'content' END AS matched_in "
                f"FROM {table} WHERE owner_id=? AND {condition} "
                f"AND (instr(lower({title}),lower(?))>0 OR instr(lower({content}),lower(?))>0)"
            )
            params.extend((query, context.owner_id, query, query))
        params.append(limit + 1)
        sql = "SELECT * FROM (" + " UNION ALL ".join(selects) + ") ORDER BY updated_at DESC,kind,id LIMIT ?"
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {"items": [dict(row) for row in rows[:limit]], "truncated": len(rows) > limit}
