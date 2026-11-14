# 审计 checkpoint 与 outbox：本地原型（2026-11-14）

## 当前交付

- 迁移 `0021` 新增 `audit_outbox`。该表仅是实验状态记录，不会触发真实上传、删除或后台任务。`(snapshot_id, action)` 约束幂等，同一键不同内容被拒绝；仅支持 `checkpoint_publish`。
- `audit_checkpoint.py` 对已提交的本地未锚定链生成 Ed25519 签名 checkpoint；包含版本、vault ID、epoch、序号、头和前一 checkpoint 摘要。签名使用规范化内容及独立域分隔；签名和完整同 epoch checkpoint 历史可由调用方提供的公钥验证。没有生成或存储正式密钥，专项测试只在内存生成虚构临时密钥。[cryptography Ed25519 文档](https://cryptography.io/en/41.0.6/hazmat/primitives/asymmetric/ed25519/)说明了签名与验签接口。
- 比较器校验本地链、签名、vault/epoch 与相应前缀。签名不正确、epoch 冲突或本地链落后于所提供的 checkpoint 返回 `ANCHOR_CONFLICT`；找不到 checkpoint 或匹配但无法确认最新时返回 `ANCHOR_STALE_OR_UNKNOWN`。**当前没有权威新鲜度源，也没有 `ANCHORED_CURRENT` 的实现路径**。公钥与 checkpoint 都由调用方提供，代码本身无法证明来源可信。
- outbox 在调用方事务和保存点内写入意图与 outcome 审计事件；提供 pending、retry、succeeded 状态、尝试次数、重试时间、受限错误代码和与 checkpoint 摘要相匹配的回执字段。相同请求幂等，过期尝试号拒绝，审计失败回滚状态变化。`succeeded` 只是本地调用者记录的结果，**不是经外部服务核实的回执**。模拟测试演示“对端已收到、本机记账前中断”后按同一键和摘要协调，但尚无真正的发送器、租约、调度器或跨进程远端事务保证。

## 风险与下一步

1. 独立可信水位、来源认证和防删改的存放位置尚未部署；签名密钥轮换、吊销和异机恢复未设计完成。旧的有效签名仍可能被重放，不能单靠签名判定最新。
2. 恢复后新 epoch 的建立、`restored_from`、旧/新 epoch 关联及外部接受尚未实现；现有代码拒绝跨 epoch 历史，避免自动信任分叉。
3. outbox 尚无领取租约、并发工作者仲裁、指数退避、持久调度、服务端回执验证和错误恢复策略；与备份创建/上传业务没有接线。后续须先设计并以故障注入验证，不能把本地状态视为交付成功。
4. 历史 `audit.log` 仍是明文；本阶段不删除或迁移它。M1 链只覆盖其明确列出的事件，其他业务仍未覆盖；G1、真实资料与正式发布依旧关闭。
5. 未连接 R2，也未读取、生成或修改用户的正式签名密钥。外部部署、真实账户和 R2 上传仍需用户单独连接并授权。
