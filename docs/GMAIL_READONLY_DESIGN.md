# Gmail 只读接入设计与离线连接器合同

**状态：** 技术设计；当前只实现本地合成数据连接器，不存在 Gmail 授权或网络调用。核对：2026-11-06。

## 分阶段授权

首个真实 Gmail 阶段仅在用户显式连接且本地加密就绪后申请读取所需范围。gmail.metadata 不含正文；gmail.readonly 可以读取正文，属于 Google 的受限范围。gmail.send 是敏感范围，gmail.compose 与 gmail.modify 属受限范围；公开发布、服务器处理受限数据时需要按适用条件评估 Google 的验证及安全评估要求，不能假设个人原型自动获得发布资格。[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)

Windows 桌面授权应使用系统浏览器、桌面应用 OAuth 客户端、PKCE、随机 state 和回环回调。Google 的桌面应用文档指出 installed app 不支持 incremental authorization；后续扩展草稿/整理/发送权限时需要按适用流程重新发起包含所需范围的授权，不能假设原授权自动扩展。[OAuth for desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app)

令牌留在用户设备安全存储中，不能写入前端脚本、同步包、日志或明文配置。浏览器端与桌面端连接路径、认证和令牌生命周期要分开设计。用户撤权后停止同步，展示本地副本清理选项；未授权时不调用真实 API。

## Gmail 只读约束

messages.list 返回的条目只含 message id 与 thread id，正文需另行 messages.get；通过 pageToken 分页。metadata scope 下不能使用 q 参数，因此查询能力不能写成所有 scope 都可用。[messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)

增量同步须保存 historyId；若 historyId 超出可用历史范围导致 404，应重新做用户授权范围内的完整同步。同步失败不得覆盖上次成功游标或本地用户整理；遇到 401/403/429/5xx 应分别提示授权或退避重试，不能静默扩大权限。[Gmail sync](https://developers.google.com/workspace/gmail/api/guides/sync)、[error handling](https://developers.google.com/workspace/gmail/api/guides/handle-errors)

## 离线合同

先用 .invalid 地址的固定合成数据集模拟只读连接器：分页、稳定外部 ID、幂等合并、断点恢复、可撤销本地连接状态与 owner 隔离。每页写入和游标推进同事务；失败时游标不前移，本地分流状态不被远端副本覆盖。该合同不含 OAuth、HTTP、真实邮箱内容、后台轮询或发送器。

真实 Gmail 的 MIME、附件、分页、history、速率限制、授权撤销与令牌轮换需用官方 API 和专门测试账户逐项验证；合成合同通过不代表真实 Gmail 连接可用。

## J8 已验证的合成实现

当前 synthetic_mail_sync_service.py 只接受进程内固定合成数据集和 .invalid 地址；用户确认后可设置单次最大读取量。分页数据写入和游标推进同事务，临时失败不前移；按模拟连接与外部 ID 去重，重复同步保留本地分流。源内容改变时使旧的待审模型预览、待处理建议与发送提案失效。撤销合成连接会停止未完成同步，但保留本地副本。

验证脚本 backend/verification/verify_synthetic_sync.py 覆盖分页、重复、失败重试、更新与本地状态保持、冲突回滚、用户设定上限、撤销和审计；不调用真实 Gmail。


## 合成同步快照一致性（2026-11-06）

模拟同步开始时绑定固定数据集内容和分页参数的散列；后续页若使用同名但内容不同的合成数据集，或取页期间数据改变，会拒绝提交并保持游标与已存数据不变。合成数据集输入总字节数有上限。旧版本正在进行的合成同步任务没有散列，应重新发起，不会假装可以安全续传。该散列只检测意外变化，不证明数据源可信，也不代表 Gmail 分页或 historyId 已实现。验证见 `backend/verification/verify_synthetic_sync.py`。


## 离线 Gmail 形状样本解析（2026-11-07）

`backend/app/services/synthetic_gmail_parser.py` 只接受明确标记的虚构样本和 .invalid 发件地址，不连接 Gmail，不下载附件，不渲染 HTML。它解析消息/会话 ID、毫秒时间、发件人、主题、嵌套 MIME 中的纯文本及附件元数据；HTML 只标记存在，附件内容和外部正文不读取。正文明确标为不可信输入，不得当作模型指令。字符集错误使用带提示的替代解码。

解析器限制头部数量/长度、MIME 层数/数量、正文大小，拒绝歧义发件人头、异常 Base64 和非合成地址；附件名称仅作显示信息，不可直接当作文件路径。它不覆盖 Gmail 的全部 MIME、附件、签名或特殊字符场景，尚未接入同步器/API；模式开关也不能识别混入的真实正文。验证见 `backend/verification/verify_synthetic_gmail_parser.py`。字段设计参照 [Gmail 消息结构](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages) 和 [消息正文结构](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments)。
