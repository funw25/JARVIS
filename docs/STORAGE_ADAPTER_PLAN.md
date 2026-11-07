# 本地存储适配与安全迁移计划

状态：设计与隔离实验；尚未改变主应用存储。2026-11-07。

## 为什么不能直接替换驱动

当前服务通过 backend/app/database.py 的 connect 上下文获取标准 sqlite3 连接。服务依赖 sqlite3.Row 的名称和位置取值、dict(row)、游标 fetchone/fetchall/迭代、写入后 rowcount、显式 BEGIN IMMEDIATE、默认 DML 事务、上下文正常提交/异常回滚，以及特定 SQLite 错误行为。迁移器还依赖事务、检查外键和历史文件校验。

候选 APSW SQLite3 Multiple Ciphers 使用不同的 Python 接口与事务默认值。单独执行现有迁移 SQL 成功不代表上述服务可直接运行；简单把 sqlite3 换成 APSW、依赖未知 SQL 前缀猜测事务、或只查看加密 PRAGMA 的返回值都会造成错误或明文风险。APSW 自身说明其事务和 DB-API 差异：[APSW 差异](https://rogerbinns.github.io/apsw/pysqlite.html)、[DB-API 说明](https://rogerbinns.github.io/apsw/dbapi.html)。

## 建议边界

- 业务用例通过显式的存储单元工作接口获取仓储和事务，不直接依赖某个驱动的隐式提交规则。读事务、写事务及需要立即获取写锁的操作应有明确入口。
- 领域代码依赖稳定的数据记录和错误类型；将驱动特定异常转换为受控的存储错误，避免把 SQL、密钥、正文或本机路径直接传给客户端。
- 将连接打开、加密配置、密钥解封、数据格式检查和应用迁移分阶段执行。任何一步失败都不创建替代明文库，不开放业务路由。
- 加密格式、算法配置、元数据版本与密钥包装需要明确存档，升级前先确保有经过验证的恢复路径。不得对现存资料自动执行不可逆的原地明文/密文转换。
- 实验适配器先放在 experimental，不作为主应用默认依赖；只使用临时虚构数据库。部署、依赖锁定、版本更新及签名审查另行完成。

## 必须用同一合同验证的行为

1. 空库初始化、重复打开、正常关闭；异常时不留下半套库或误建明文文件。
2. 单条和多条写入的提交/回滚；写入与审计、来源关联、幂等键必须同事务成功或失败。
3. 显式读事务、立即写锁、并发写入/删除竞争、忙锁和超时；不同字段更新不得悄悄互相覆盖。
4. 行对象的按名称/位置读取、缺列行为、重复列名、NULL、Unicode 和 dict 转换；游标行数与异常映射符合调用方合同。
5. 用真实的当前迁移文件验证新库、旧版虚构库升级、校验和不匹配、迁移失败完整回滚和重复启动。
6. 在独立密钥下验证正确/错误密钥、库与 WAL、异常进程退出、损坏数据、丢失/损坏密钥、连接缺失，所有失败关闭且无明文回退。
7. 通过服务层和真实 loopback HTTP 端到端回归邮件、待办、知识、审批、隔离和重复请求；再由外部安全评审检查日志、临时文件、包来源、密钥生命周期及访问保护。

## 分阶段推进

1. ✅ **已完成（2026-11-07）**：为现有 sqlite3 实现建立行为合同和失败注入（`verify_storage_contract.py` + `verify_storage_transactions.py`），未变更默认存储。
2. 🟡 **进行中**：实验目录已实现 APSW SQLite3MC 适配器并跑通同一套合同（见下节）；**尚未**扩展到整个合成服务测试集，也没有迁移运行器和密钥生命周期。
3. 单独设计经过验证的备份和换机恢复、设备撤销、可信本机会话与产品迁移体验。
4. 独立安全评审和发布准备完成后，才与用户讨论导入真实资料或连接服务。

当前 DPAPI 实验只绑定本机 Windows 用户；没有恢复材料时不能承诺换机恢复。已解锁设备上的同用户恶意程序不在单靠磁盘加密可解决的范围内。

## 适配器实验：APSW SQLite3MC（2026-11-07）

**实现：** `backend/experimental/apswmc_storage.py`，提供与 `app.database.connect` 同形的上下文管理器：

- `connect(path, key, create=True)`：32 字节原始密钥，`PRAGMA cipher=chacha20` + `PRAGMA key=raw:<hex>`，强制 `temp_store=MEMORY`、`foreign_keys=1`；打开时读一次 schema，因此错误密钥在打开阶段即失败，不会静默降级为明文；拒绝通过符号链接打开。
- 事务语义与 sqlite3 实现对齐：**第一条写语句前自动 `BEGIN IMMEDIATE`**，正常退出提交、异常回滚、关闭前若仍有事务则回滚。
- 行对象补齐 `sqlite3.Row` 的子集：按名/按位置取值、`keys()`、`dict(row)`、缺列抛 `IndexError`；`rowcount` 由 `changes()` 派生，`lastrowid` 对应 `last_insert_rowid()`；游标提供 `fetchone/fetchall/fetchmany/迭代`。
- **本构建的限制**：`apsw-sqlite3mc` 4.x 只暴露模块级 `apsw.mc_version`，**没有** `PRAGMA cipher_version`；因此加密是否生效由“cipher 回显 + 文件头非明文 + 错误密钥被拒 + 明文 sqlite 被拒”实测确认，而不是读一个不存在的 pragma。

**验证：** `backend/verification/verify_apswmc_adapter.py`（注册为 `apswmc_adapter`），9 组断言全部 PASS：

1. 门禁拒绝且不创建数据库文件；2. 反复开关后 `integrity_check` 为 ok；3. 未提交内容对另一连接不可见；4. 首写持 `BEGIN IMMEDIATE`，零超时写者被拒；5. `busy_timeout` 会等待（实测 ≥250 ms）；6. 陈旧写入被 `rowcount=0` 检出而非覆盖；7. 行对象/keys/dict/NULL 与空串/中文与 emoji/rowcount/游标；8. 关闭后拒绝使用；9. 密文、错误密钥、明文 SQLite 三重拒绝。

**明确未做：** 密钥生命周期与恢复、备份/换机、WAL 与并发压力、许可证与发布审查、独立安全评审。这些完成前不得替换默认存储。

### 注入点与开关（2026-11-07 新增）

`app/database.py` 现在把连接选择显式化，**默认行为不变**：

- `connect_sqlite(path)`：原实现，默认路径；
- `connect(path)`：按 `JARVIS_STORAGE_ADAPTER` 选择后端（未设置时走 sqlite3）；
- 只有 `JARVIS_STORAGE_ADAPTER=apswmc` + `JARVIS_APSWMC_KEY`（64 位 hex）同时存在时才导入 `experimental.apswmc_storage`，并把它所在的 `backend` 与探针目录按需加入 `sys.path`；密钥缺失或非法时抛 `StorageAdapterError` 失败关闭，不会回落到明文。
- 服务在导入期绑定 `..database.connect` 这个**名字**，因此不需要改 22 个服务模块；开关按调用解析，同一进程内可切换。

### 服务层验证（2026-11-07 新增）

`backend/verification/verify_apswmc_services.py`（注册为 `apswmc_services`）在**加密库**上跑真实领域服务，9 组断言全部 PASS：

1. 开关被识别并完成 17 个迁移；2. 迁移后的库是密文，明文 sqlite3 无法读取；3. 待办创建/幂等键/状态流转；4. 待办归属隔离 + 只读分享 + 撤销（都在密文上进行）；5. 知识收集项 → 笔记；6. 会话消息历史；7. 续聊快照接受并开新会话；8. 审计行与领域数据在同一加密库；9. 跨连接重开仍能读到数据（持久性）。

**整套回归已可在加密后端运行（2026-11-07）：** `backend/verification/verify_adapter_suite.py` 用随机密钥以 `JARVIS_STORAGE_ADAPTER=apswmc` 启动 `verify_all.py`，整套 **49 项全部通过**（0 FAIL / 0 SKIP），默认后端同样 49/0/0。为做到这一点改了三处验证脚本：

- 新增 `backend/verification/storage_view.py`：`open_database()` / `rows()` / `table_names()` 统一走注入的连接，并按后端合成异常元组（`DATABASE_ERRORS` / `CONSTRAINT_ERRORS` / `OPERATIONAL_ERRORS`）；探针目录只在 3.14 + win32 且模块完整时才加入 `sys.path`。
- `verify_migrations.py`：全部 `sqlite3.connect(...)` 检查改为 `storage_view.open_database(...)`，行比较改为 `tuple`，异常改抓后端无关元组。
- `verify_storage_contract.py`：约束异常改抓 `CONSTRAINT_ERRORS`。
- `verify_storage_transactions.py`：隔离/丢失更新改走注入连接；两项“零超时原始连接”探针在加密后端下显式标记 `SKIPPED_ON_ADAPTER`（等价覆盖在 `verify_apswmc_adapter.py`）。

**适配器侧顺带修的问题：** 语句未释放导致 `COMMIT` 报 `BusyError`（改为提交前 finalize 所有游标）；默认 DELETE journal 下读锁阻塞写提交（改为 WAL + `busy_timeout` + 切换重试）；`Row` 需要与 tuple 按值相等（迁移与业务断言依赖）；父目录不存在时无法建库（补 `mkdir`）。

**S4 并发阶段又发现并修掉两个更严重的问题（2026-11-07）：**

1. `BEGIN IMMEDIATE` 之前不释放旧语句。SQLite 对 `SQLITE_BUSY_SNAPSHOT` **不调用** busy handler，所以任何“先读一行、再写”的用法都会在 20 ms 内直接 `BusyError`。修法：`begin_immediate()` 先 finalize 所有游标再开事务。
2. 连接的活跃游标集合用的是强引用，一次性查询（如 `PRAGMA integrity_check`、`SELECT ... fetchone()`）被丢弃后语句不会释放，读快照永不结束——读者会**永远读到旧值**。修法：改为 `weakref.WeakSet`，让被丢弃的游标随垃圾回收释放语句；实测修复后读者能看到 0→1→2→3→4→5 的提交流。

**仍未覆盖：** 备份/换机恢复、密钥生命周期与轮换、WAL 并发压力测试、许可证与发布审查、独立安全评审。这些完成前不得把默认存储切到加密后端。

## 已验证的基线

`verify_storage_contract.py` 在临时虚构 SQLite 库验证按名称/位置读取、重复列名、rowcount、约束异常、提交与回滚；在待办创建及从收集项创建笔记时注入审计失败，确认待办、请求键、笔记和来源状态均不留下部分写入。

`verify_storage_transactions.py`（2026-11-07 新增，对应上面合同第 1–4 条）另外覆盖：

1. **门禁无副作用**：未配置 `JARVIS_DATA_MODE` 时拒绝访问，且不创建目录或数据库文件；
2. **反复打开/关闭**：三次开关后 `PRAGMA integrity_check` 仍为 ok；
3. **读事务隔离**：同一连接未提交的写入对外部连接不可见，提交后可见；
4. **立即写锁**：`BEGIN IMMEDIATE` 持锁期间第二个写者以 `timeout=0` 得到 locked 错误，释放后可获取；
5. **忙锁等待**：`PRAGMA busy_timeout` 会等待持有者提交（实测等待 ≥150 ms）而不是立即失败；
6. **丢失更新可检出**：两个写者基于同一旧值更新，后者 `rowcount=0`，不会被静默覆盖；
7. **行对象边界**：缺列抛 `IndexError`、NULL 与空串可区分、中文和 emoji 往返、DELETE 的 rowcount、游标迭代与 `fetchmany`；
8. **关闭后拒绝使用**：已关闭连接上的语句抛 `ProgrammingError`。

它只覆盖现有 sqlite3 实现，**尚未对 APSW 适配器运行**，也不涉及加密、备份或恢复。下一批合同（迁移升级/校验和/断电恢复的服务层回归）仍需在适配器落地时补齐。
