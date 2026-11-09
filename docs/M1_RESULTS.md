# M1 离线纵向闭环：结果与限制

**状态：** 已交付数据集、规则基线、纵向演示与评测框架；**模型质量未验证**  
**日期：** 2026-11-08  
**数据：** 全部虚构；未连接任何账户、模型或外部服务  
**代码：** `backend/evals/m1/artifacts.jsonl`、`backend/usecases/m1_*`、`backend/migrations/0018_m1_artifacts.sql`  
**验证：** `verify_m1_dataset` / `verify_m1_pipeline` / `verify_m1_evaluation`（已注册进 60 项回归）

---

## 1. 标注集

| 项 | 值 |
|---|---|
| 条目 | **53**（要求 40–60） |
| 类别分布 | needs_reply 15、action 13、fyi 11、noise 6、injection 8 |
| 长线程（thread_len ≥ 5） | 6 |
| 重复组（正文完全相同） | 3 |
| 含糊承诺（"有空/下次/sometime"） | 4 |
| 高优先级 | 20 |
| 证据 | 每条 1–3 个**逐字**片段，脚本校验必须是正文子串 |

覆盖：明确请求、英文请求、截止日期（今天/明天/本周五/下周/月底）、长线程追问、两步指令、软性任务、纯通知、营销与摘要、钓鱼与提示注入、含糊约定。

## 2. 规则基线（rules-v2）

| 指标 | 结果 |
|---|---|
| 分类准确率 | **100.0%**（53/53） |
| todo 判断准确率 | 88.7%（47/53） |
| draft 判断准确率 | 100.0% |
| 优先级准确率 | 94.3%（50/53） |
| 证据非空率 | 90.6% |
| 证据逐字率 | 90.6% |
| **注入识别召回** | **100.0%**（8/8） |
| 单次评测耗时 | 约 2 ms |

**必须声明的偏差：标注集与规则由同一人同时编写**，规则使用了标注集里的词汇。因此 100% 分类准确率**不代表泛化能力**，评测报告里以 `dataset_authored_with_rules: true` 与 `generalization_claimed: false` 固定写入。作为反面对照，一个永远答 `fyi` 的坏引擎在同一集合上只有 **20.7%**，说明评测确实能区分好坏。

规则基线的真实错误（全部列出，不做掩饰）：

| 类型 | 条目 |
|---|---|
| todo 漏判（应为 true） | m1-003、m1-006、m1-007、m1-011、m1-049 |
| todo 误判（应为 false） | m1-012 |
| 优先级偏差 | m1-008（low→normal）、m1-011（high→normal）、m1-012（low→normal） |
| 无证据（纯 fyi、无触发词） | m1-027、m1-029、m1-030、m1-033、m1-051 |

## 3. 纵向闭环

`python backend/usecases/m1_cli.py demo --id m1-005` 的实测路径：

```
artifact_imported  -> m1_artifact
suggestion_created -> m1_suggestion   (needs_reply / high / todo + draft / 证据逐字)
decision_recorded  -> m1_decision     (accept 或 modify + 用户改写)
todo_created       -> todo            (priority=high, status=open)
draft_created      -> m1_draft        (正文标"未发送"，无发送器)
```

已验证的分支（`verify_m1_pipeline.py`）：

| 场景 | 断言 |
|---|---|
| 正常接受 | 5 条审计事件齐全；todo 与 draft 同时生成 |
| 用户修改 | `{"todo": false, "title": ...}` → 不建 todo，只建 draft，标题用用户的 |
| 拒绝 | 不产生任何行，但写 `decision_recorded` |
| 重复文本 | 第二次导入写 `duplicate_of`，审计记录 `duplicate` 标记 |
| 注入（接受） | 分类 `injection`、优先级 high，且**不产生 todo/draft** |
| 发送能力 | 库内不存在任何"已发送"草稿；无发送器代码路径 |

## 4. 模型质量：未验证

- 当前没有配置任何真实模型；`local_fake` 只证明流程可跑，**不能**替代质量基线。
- `m1_eval.evaluate_model(rows, None)` 直接抛 `ModelUnavailable`，拒绝编造分数；`model_status()` 返回 `{"status": "未验证", "measured": false}`。
- 验收标准保持：没有真实模型时，M1 的质量一栏就是"未验证"，不以新增测试数量替代。

## 5. M1 完成度对照（[执行计划 §3](JARVIS_V2_EXECUTION.md)）

| 工作包 | 状态 |
|---|---|
| 1. 固定样本与契约（40–60 条标注） | ✅ 53 条，含证据与类别 |
| 2. 画清一条纵向调用链 | ✅ `usecases/m1_rules|pipeline|eval` + 迁移 0018；尚未把旧 `synthetic_*` 全面端口化 |
| 3. 可用演示 | ✅ CLI 一条命令跑完整链路并打印审计 |
| 4. AI 基线（规则 + 模型对比） | 🟡 规则基线完成；模型基线**未验证** |
| 5. 固定运行环境 | ❌ 未做（属 M2） |

M1 **未完成**：模型质量一栏为空、运行环境未锁定。按执行计划，未达到定义就留在 M1。

## 6. 限制

- 53 条样本量小，且与规则同源；不能用于对外宣称准确率。
- 只有中文与少量英文，没有真实长邮件、附件、HTML、线程引用链。
- 没有真实模型，因此没有质量/耗时/费用对比。
- 页面（HUD）未接入；M1 只提供 CLI 与数据库状态。
- 规则是关键词匹配，对同义改写、反讽、多语言混合没有鲁棒性证据。
