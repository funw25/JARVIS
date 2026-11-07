# Todo service contract (B1)

Todo use cases live in the local domain service; no HTTP routes expose them yet. Every operation receives an AssistantContext created by trusted application code.

- Create requires explicit user confirmation. Titles, descriptions, priority and due timestamps are validated; due times must include a timezone and are normalized to UTC.
- Tasks are visible to their creating assistant only by default. A user-confirmed share grants the other assistant read-only access; only the creator can edit, change status, delete, restore, or revoke that share.
- Supported lifecycle: open, in_progress, done, cancelled. A cancelled task can only be reopened; completing records completed_at, and reopening clears it. Delete is soft and can be restored.
- Sources are limited to messages, approved memories, accepted continuity snapshots, same-owner synthetic mail, captures and notes visible to Jarvis. Shared task readers do not receive the creator's source links.
- Every state change records metadata-only audit events.

This is not an authentication boundary and data is not encrypted. Use synthetic data only until the local security milestone is complete.


## 并发与授权补强（2026-11-06）

待办更新、状态、删除/恢复和分享/撤销在读改写前获得写入事务，防止经服务并行修改时覆盖不同字段；重复完成不更改完成时间。对同一对象和目标助手的重复分享复用已有授权；撤销时同时撤销历史重复授权，避免表面撤销而仍可访问。测试见 `backend/verification/verify_todo_concurrency.py` 和 `verify_todo_shares.py`。条件写版本、跨设备冲突解决、生产会话认证和真实数据加密仍未完成。

默认待办列表在同一状态组内先显示有截止时间的事项，再按时间排序；未设置截止时间的事项排在后面。该行为用合成任务验证。

## 重复提交（合成数据）

创建待办的领域服务支持可选的调用方请求键：同一 owner、助手、键和规范化内容重复提交返回原 ID；同键不同内容被拒绝。并发请求与来源检查、创建、请求记录和审计位于同一事务。待办删除后，重试原请求只返回旧 ID，不重新创建或恢复。请求键应由客户端随机生成，不能放正文或密码；有序来源列表不同会视为不同内容。邮件和知识转待办的领域流程也支持此键；此能力尚未接 HTTP，不能用于对外发送。

验证：`backend/verification/verify_todo_idempotency.py` 覆盖并发重试、不同内容冲突、owner/助手隔离、删除后重试和来源失败回滚。
