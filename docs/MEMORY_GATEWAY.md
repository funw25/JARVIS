# Memory Gateway contract (B1)

The Memory Gateway is the only application service allowed to retrieve assistant memories. There is deliberately no HTTP endpoint yet. A future caller must obtain AssistantContext from authenticated server state; it must never accept owner_id or assistant_id as trusted request data.

## Default visibility

- companion can read approved items in its companion namespace.
- jarvis can read approved items in its work namespace.
- conversation_raw is visible only to the assistant that created the item.
- profile_shared and another assistant's namespace are hidden unless the user grants access to that exact item or namespace.
- candidates, forgotten, deleted and expired items are excluded from retrieval.
- a revoked grant is excluded on the next query.

## Writes

Assistant output creates a candidate. Only explicit approval changes candidate to approved. Forgetting excludes an item from retrieval while preserving the row for potential user recovery; deleting removes the memory, source links and item-specific grants. Sharing requires an approved item or an owned assistant namespace.

## Audit

Reads and state changes append metadata-only audit rows; memory text is never copied into audit metadata.

## Current limitations

This stage is a local domain service, not a secure account boundary. It has no login identity provider, encryption, API routes, concurrency policy, quota, or restore flow. Use synthetic data only. Static checks and tests have not been run in the restricted Windows shell. Before real data, implement local encryption and a trusted owner/session context; before exposing routes, add authn/authz and request origin protections.
