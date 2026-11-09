# Jarvis 项目文档

这套文档是 Jarvis 的长期产品记录。产品范围、界面语言、系统边界、决策和版本计划分别放在独立文件里；重要变化应同步更新相应文档与本地原型。

**2026-11-08 重审入口：** [新版产品定义](JARVIS_V2_PRODUCT.md) → [新版目标架构](JARVIS_V2_ARCHITECTURE.md) → [当前进度与下一阶段](JARVIS_V2_EXECUTION.md)。这三份是新的独立建议方案；下方旧版需求、路线图和实验文档保留为证据与历史记录。陪伴需求仍冻结，真实资料仍禁止进入当前构建。

## 文档索引

- [新版产品定义与版本边界](JARVIS_V2_PRODUCT.md)
- [新版目标架构与 AI 算法设计](JARVIS_V2_ARCHITECTURE.md)
- [新版交付计划、当前进度与下一阶段](JARVIS_V2_EXECUTION.md)
- [39 项风险整改选项](RISK_REMEDIATION_OPTIONS.md)
- [当前进展、未完成事项和下一步](CURRENT_STATUS.md)
- [从这里开始：本机演示与启动说明](START_HERE.md)
- [2026-11-06 工作进展检查点](CHECKPOINT_2026-11-06.md)
- [多设备密文同步设计草案](SYNC_DESIGN_DRAFT.md)
- [Jarvis 后端接口合同草案](JARVIS_API_CONTRACT_DRAFT.md)
- [待办与跨资料检索](JARVIS_TASK_SEARCH.md)
- [本地待办提醒规则](TODO_REMINDERS.md)

- [底层优先实施方案](CORE_IMPLEMENTATION_PLAN.md)
- [Jarvis 合成邮件工作流](JARVIS_MAIL_WORKFLOW.md)
- [Gmail 只读接入设计](GMAIL_READONLY_DESIGN.md)
- [Jarvis 收集与知识工作流](JARVIS_KNOWLEDGE_WORKFLOW.md)
- [本地数据保护与恢复方案](DATA_SECURITY_DESIGN.md)
- [密钥生命周期、备份与恢复设计](KEY_LIFECYCLE_AND_RECOVERY.md)
- [存储适配与安全迁移计划](STORAGE_ADAPTER_PLAN.md)
- [切换到加密存储的计划](STORAGE_SWITCH_PLAN.md)
- [安全评审包](REVIEW_PACKET.md)
- [加密驱动审查清单与自建草案](DRIVER_REVIEW_AND_BUILD.md)
- [独立合成加密库实验](EXPERIMENTAL_SYNTHETIC_VAULT.md)
- [Python 3.14 隔离兼容性实验](ISOLATED_PYTHON_314.md)
- [当前安全问题与验收门槛](SECURITY_REVIEW.md)
- [本地模拟模型的数据流与确认](MODEL_DATA_FLOW.md)
- [Jarvis 本地总览](JARVIS_OVERVIEW.md)
- [合成邮件整理规则](MAIL_RULES.md)
- [数据库迁移可靠性](DATABASE_MIGRATIONS.md)
- [合成资料导出预览](SYNTHETIC_EXPORT.md)
- [细化方案与实施计划（历史原型）](PLAN.md)
- [项目日志与下一步](PROJECT_LOG.md)
- [产品需求与范围](PRODUCT_REQUIREMENTS.md)
- [视觉与交互设计](VISUAL_DESIGN.md)
- [系统架构](ARCHITECTURE.md)
- [外部集成与统一收集方案](INTEGRATIONS.md)
- [对话中枢：跨平台私信与持续聊天](MESSAGE_HUB_REQUIREMENTS.md)
- [双助手与连续性记忆设计](DUAL_ASSISTANT_DESIGN.md)
- [路线图与验收标准](ROADMAP.md)
- [设计决策记录](DECISIONS.md)
- [本机环境问题诊断（pwsh / 0xC0000142）](ENVIRONMENT.md)

## 当前状态

- 阶段：Jarvis 优先；邮件、待办、知识、检索与模拟审批的底层合成数据测试和部分本机 HTTP 验证通过；应用集成加密与恢复仍未实现
- 界面：v0.3 浏览器原型冻结；前端暂缓，陪伴通道保留但暂停扩展
- 数据：纯模拟数据
- 服务连接：未实现、未授权
- 外部写操作：未实现
- 同步目标：本地优先，端到端加密，云端只保存密文；协议尚未实现
- 收集目标：微信、X、浏览器、小红书、邮件及其他应用；首版按来源采用官方 API 或用户主动捕获

文档状态标签：`Accepted` 已采纳；`Proposed` 建议中；`Open` 需要产品决定。
