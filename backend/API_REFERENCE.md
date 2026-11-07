# Local API v1 reference

Base path: /api/v1. The server binds to 127.0.0.1:8765 by default. Startup requires explicit JARVIS_DATA_MODE=synthetic; this mode is not encrypted and is for synthetic data only. Every business request requires:

- Authorization: Bearer <JARVIS_LOCAL_API_TOKEN>（32–128 个 URL 安全 ASCII 字符；建议用安全随机数生成，格式检查不能保证随机性）
- X-Jarvis-Assistant: companion or jarvis (except GET /assistants)
- JSON for request bodies；未知字段会被拒绝，不能通过请求体设置 owner

The owner is resolved from local process configuration, never from a body or URL parameter. The same token maps to one local owner. Changing X-Jarvis-Assistant only selects an identity; service-level namespace checks still apply.

## Routes

- GET /health — local service status (no personal data).
- GET /assistants — available assistant identities.
- GET/POST /conversations — list or create within the selected assistant.
- GET/POST /conversations/{id}/messages — read or append within that same assistant's conversation.
- GET /memories; POST /memories/candidates; POST /memories/{id}/approve; POST /memories/{id}/forget; DELETE /memories/{id}.
- POST/DELETE /memory-shares and /memory-shares/{grant_id}.
- POST /continuity/drafts; POST /continuity/{id}/discard; POST /continuity/{id}/accept.
- GET/POST /todos; GET/PATCH/DELETE /todos/{id}; POST /todos/{id}/status; POST /todos/{id}/restore.
- POST /todos/{id}/shares; DELETE /todo-shares/{grant_id}.

## Response behavior

- 401 missing/invalid bearer token; 403 rejected origin or access denial; 404 object outside the current owner's/assistant's scope; 409 invalid state transition; 422 invalid input; 503 local API token is not configured. Pydantic request-validation errors return at most 20 sanitized field locations and error types, without echoing submitted values. Invalid Unicode and excessively nested/large model structures are rejected. Service-level 422 errors may retain their own short messages.
- Responses are non-cacheable. The API does not enable CORS. Host and browser Origin are allowlisted for loopback use. Duplicate Authorization, X-Jarvis-Assistant, Sec-Fetch-Site and Content-Type headers are rejected.
- POST to memory approval/share, continuity acceptance, todo create/share are explicit user-directed calls; the model itself has no HTTP credentials or direct route access.

## Limitations

This is a single local owner gate, not account login or a multi-device session system. The bearer secret is supplied through JARVIS_LOCAL_API_TOKEN; secure OS credential storage, token rotation/expiry, CSRF integration for a future same-origin UI, rate limits, and production deployment policy remain future work. Do not put the token in source control, URLs, browser localStorage, logs, or screenshots. Until local database encryption is implemented, use synthetic data only.


## Jarvis 本机模拟 API（2026-11-07）

以下路由已通过临时合成数据的 loopback HTTP 测试。均要求开发 bearer token 与 `X-Jarvis-Assistant: jarvis`；仅限 synthetic 存储模式。开发 token 不构成生产身份认证或用户在场证明。禁止录入真实私人资料。

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| GET /jarvis/overview | 返回本地合成总览，支持 utc_offset_minutes、limit |
| GET /jarvis/search | query 必填；可重复 kind，限制返回数量 |
| GET /jarvis/tasks | 按 query、status、priority、截止范围分页 |
| GET /jarvis/knowledge/{kind} | kind 为 capture/note；按 query、tag 和 limit 分页，capture 可加 status；不返回正文 |
| GET /jarvis/mail | 本地虚构收件箱，可按 query（主题/发件人）、重复 status、priority 和 limit 筛选，cursor 翻页；默认不含已归档，不返回正文 |
| GET /jarvis/mail/threads | 汇总本地合成邮件会话，不返回正文 |
| GET /jarvis/mail/connections/{connection_id}/threads/{thread_id} | 读取指定模拟连接下会话的摘要 |
| POST /jarvis/synthetic/mail | 创建虚构邮件，发送者必须为 .invalid 地址 |
| GET /jarvis/synthetic/mail/{mail_id} | 查看当前 owner 的合成邮件 |
| PATCH /jarvis/synthetic/mail/{mail_id}/triage | 修改本地分流与优先级 |
| POST /jarvis/synthetic/mail/{mail_id}/tasks | 用户提交后创建关联待办，支持 Idempotency-Key |
| POST /jarvis/synthetic/mail/{mail_id}/drafts | 创建合成草稿，账号与收件地址必须为 .invalid |
| GET /jarvis/synthetic/drafts | 草稿摘要列表，可按 mail_id 筛选和翻页，不含正文/收件人 |
| GET / PUT /jarvis/synthetic/drafts/{draft_id} | 查看或编辑合成草稿 |
| POST /jarvis/synthetic/drafts/{draft_id}/send-proposals | 创建不可发送的模拟审批快照 |
| GET /jarvis/synthetic/send-proposals | 未过期待审批列表，按到期时间分页，不含载荷 |
| GET /jarvis/synthetic/send-proposals/{proposal_id} | 查看模拟审批；失效/过期快照不返回正文 |
| POST /jarvis/synthetic/send-proposals/{proposal_id}/approve | 提交所展示的 confirmed_hash，只改变模拟状态 |
| POST /jarvis/synthetic/send-proposals/{proposal_id}/reject | 拒绝模拟审批 |

`POST /todos` 也支持可选 `Idempotency-Key`。同一 owner、助手、键和同一规范化内容重复创建返回原 ID；不同内容返回 409，重复请求头返回 422。应使用随机请求 ID，不要把正文或密钥放在请求头。删除后重试不会恢复待办。

审批接口不连接任何供应商，返回 `execution_available: false`，没有发送执行路由。普通 HTTP 请求中的确认不能证明真实用户在场；真实邮件写操作必须另行实现可信确认、执行幂等和身份保护。


### 本地模拟收集与知识接口

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| POST /jarvis/synthetic/captures | 保存虚构文字或 .invalid 链接 |
| GET /jarvis/synthetic/captures/{id} | 查看当前 owner 的模拟收集项 |
| PATCH /jarvis/synthetic/captures/{id}/status | 修改本地状态 |
| DELETE /jarvis/synthetic/captures/{id} | 软删除模拟收集项 |
| POST /jarvis/synthetic/captures/{id}/restore | 恢复模拟收集项 |
| POST /jarvis/synthetic/notes | 创建模拟笔记，可关联模拟收集项 |
| GET / PUT / DELETE /jarvis/synthetic/notes/{id} | 查看、修改或软删除模拟笔记 |
| POST /jarvis/synthetic/notes/{id}/restore | 恢复模拟笔记 |
| POST /jarvis/synthetic/captures/{id}/tasks | 从模拟收集项创建待办，支持 Idempotency-Key |
| POST /jarvis/synthetic/notes/{id}/tasks | 从模拟笔记创建待办，支持 Idempotency-Key |

本机开发接口限制请求体最多 1 MiB、接收最长 15 秒；普通和分片请求均受限制。此机制没有连接数或整体资源配额，不能替代生产服务的反向代理、限流与身份认证。


### 本机假模型接口

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| POST /jarvis/synthetic/model/requests | 准备本机假模型预览，显示来源、用途和完整模拟载荷 |
| GET /jarvis/synthetic/model/requests/{id} | 查看预览或结束状态 |
| POST /jarvis/synthetic/model/requests/{id}/cancel | 取消预览并逻辑清空当前载荷 |
| POST /jarvis/synthetic/model/requests/{id}/execute | 提交 confirmed_hash，执行一次本机假模型 |
| GET /jarvis/synthetic/model/suggestions/{id} | 查看本机模拟建议 |
| POST /jarvis/synthetic/model/suggestions/{id}/reject | 拒绝建议并清空当前正文 |
| POST /jarvis/synthetic/model/suggestions/{id}/drafts | 用户提交编辑后的合成草稿 |

这些操作只使用进程内假模型，输出明确标记为模拟。它们不连接任何模型供应商；普通开发 HTTP 请求不能证明真实用户在场，不得作为未来云模型发送的生产授权。

### 本地合成待办提醒

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| GET /jarvis/synthetic/reminders/rules | 列出本地规则 |
| POST /jarvis/synthetic/reminders/rules | 指定 todo_id 和 offset_minutes（0–10080）创建规则 |
| PATCH /jarvis/synthetic/reminders/rules/{id}/enabled | 用布尔值 enabled 启用或停用 |
| GET /jarvis/synthetic/reminders/due | 按触发时间列出未确认提醒，limit 为 1–100 |
| POST /jarvis/synthetic/reminders/{id}/acknowledge | 提交当前 token 确认；旧快照返回 409，同次重复确认可重试 |

只查询与记录，不会产生系统通知。token 是快照校验值，不是身份认证或真实用户在场证明。正常生产系统仍需可信会话、数据加密及权限管理。

### 本地合成邮件整理规则

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| GET /jarvis/synthetic/mail-rules | 列出当前 owner 的本地规则 |
| POST /jarvis/synthetic/mail-rules | 创建默认停用的条件与目标状态规则 |
| PATCH /jarvis/synthetic/mail-rules/{id}/enabled | 明确启用或停用；重复设置同一状态不会使预览失效 |
| POST /jarvis/synthetic/mail-rules/{id}/previews | 生成有期限的影响预览，不包含邮件正文 |
| GET /jarvis/synthetic/mail-rule-previews/{id} | 待读取的预览带计划和散列，已关闭预览不返回计划 |
| POST /jarvis/synthetic/mail-rule-previews/{id}/apply | 提交当前 confirmed_hash 以原子整理合成邮件 |
| POST /jarvis/synthetic/mail-rule-previews/{id}/undo | 撤销未被后续修改的项目，报告冲突数量 |

规则只操作本机虚构邮件，不连接真实邮箱，不后台运行。确认是开发请求，不能证明用户在场。新关闭的预览会清空当前数据库行中的计划，撤销所需的状态变化记录仍保留；历史关闭快照不会自动清理，过期也仅在访问时处理。旧数据库页面、备份和 WAL 可能保留原内容，这不是物理擦除。

### 本地合成知识标签

| 方法与路径（前缀 /api/v1） | 行为 |
|---|---|
| GET /jarvis/synthetic/knowledge-tags/search?tag=... | 精确查找有该标签的未删除收集项/笔记；limit 最多 100 |
| GET /jarvis/synthetic/knowledge-tags/{kind}/{id} | 列出单个对象的标签；kind 为 capture 或 note |
| POST /jarvis/synthetic/knowledge-tags/{kind}/{id} | 用 JSON tag 添加标签；重复请求不重复创建 |
| POST /jarvis/synthetic/knowledge-tags/{kind}/{id}/remove | 用 JSON tag 移除标签；重复请求返回 removed=false |

软删除隐藏对象和标签，恢复后标签重新可见。仅用于本地虚构资料。

收件箱游标绑定当前 owner 和筛选条件，只表示位置而非授权或快照；翻页期间数据变化可能影响结果。大小写匹配受 SQLite lower 行为限制，不能保证完整 Unicode 大小写规则；正文检索使用已有统一搜索。

草稿和待审批列表不包含草稿正文、收件人或审批载荷，必须打开详情核对；过期待审批项从列表排除，但列表查询不主动更改其状态或清空数据库中的历史载荷。游标仅定位，非授权或快照。没有执行器。
