# 密钥生命周期、备份与恢复设计

**状态：** S1–S3 已实现并验收（`vault_keys.py`、`vault_backup.py`、`backup_sink.py`）；S4/S5 待做  
**更新：** 2026-11-07  
**范围：** 加密存储正式启用前的最后一段设计：密钥生成/解锁/轮换/销毁、备份与换机恢复、崩溃与并发恢复  
**关联文档：** [数据保护与恢复方案](DATA_SECURITY_DESIGN.md)（威胁模型与候选调研）、[存储适配计划](STORAGE_ADAPTER_PLAN.md)（适配层与合同）、[多设备同步草案](SYNC_DESIGN_DRAFT.md)（跨设备传输）

> 本文只写设计与验收口径，不含代码。当前主应用仍是明文 SQLite + synthetic 门禁；加密后端仅在 `JARVIS_STORAGE_ADAPTER=apswmc` 下用于验证。

---

## 1. 目标与非目标

**目标（按优先级）**

1. 磁盘上的数据库、WAL/SHM、临时文件、备份都只有密文；密钥不落明文文件、不进日志、不进环境变量的持久化配置。
2. 密钥可**轮换**，轮换失败可回滚，旧密钥立即失效且能被验证。
3. 能在本机**崩溃/断电**后恢复；能识别损坏与不匹配并拒绝打开，而不是静默降级。
4. 用户能**备份**并获得可验证的恢复能力；明确区分"本机可解锁"与"换机可恢复"。
5. 每一步都有可执行的验收脚本，失败即阻塞发布。

**非目标（明确不做）**

- 不把密钥托管给云端，不提供"忘记口令也能找回"的客服式恢复。
- 不防御已解锁设备上的同用户恶意程序、内存转储、键盘记录、管理员级攻击。
- 不在本设计中解决跨设备 E2E 同步（见 SYNC 草案），只定义单机密钥与备份格式。
- 不承诺软删除等于安全擦除：旧备份、旧设备、文件系统残留不在磁盘加密可解决范围。
- 不引入自创密码协议；参数与算法只从受支持的标准实现中选取。

---

## 2. 密钥层级

```
用户口令（可选，仅恢复包使用）
        │  Argon2id（参数记录在恢复包清单里）
        ▼
   KEK_recovery ──包装──► DEK ──┬─► SQLite3MC（chacha20，raw key）
        │                       │
        │                       └─► 备份包中的副本（可选）
        ▼
   DPAPI(当前用户) ──包装──► DEK      ← 本机日常解锁路径

DEK：32 字节随机；每个 vault 一个；轮换只换 DEK
KEK：不由用户记忆，派生或系统托管；绝不与 DEK 同存明文
```

**为什么两层**：DPAPI 绑定 Windows 用户与机器，日常打开无需口令，但**无法换机**；口令派生的 KEK 可跨机器，但必须由用户保存。两者包装同一个 DEK，互不替代，也互不削弱——任一路径泄露都不应导致另一路径失效。

**密钥标识**：每个 DEK 带 `key_id`（16 位十六进制随机串）。所有文件、清单、审计事件都用 `key_id` 关联，禁止用文件名或时间戳推断。

---

## 3. 文件布局

```
<data dir>/vault/
  meta.json                 格式版本、当前 key_id、创建/轮换时间、驱动信息
  dek.dpapi                 当前 DEK 的 DPAPI 包装（用户范围）
  dek.recovery.<key_id>     可选：口令派生 KEK 包装的恢复副本
  vault.db                  加密数据库
  vault.db-wal / -shm       仅运行时存在；不得进入任何备份之外的副本
  retired/                  轮换后退役的包装文件（只读留档，不含明文）
  audit.log                 追加式：key_created / unlocked / rotated / backup / restored / destroyed
```

`meta.json` 字段（示例，非最终 schema）：

```json
{
  "format": "jarvis-vault-v1",
  "key_id": "9f3c1a70b2d84e51",
  "created_at": "2026-11-07T10:00:00Z",
  "rotated_at": null,
  "cipher": "chacha20",
  "driver": {"name": "apsw-sqlite3mc", "version": "3.54.0.0", "cipher_version": "2.6.1"},
  "backup": {"last_at": null, "last_key_id": null}
}
```

**禁止**：`meta.json`、`audit.log`、日志与错误信息中出现密钥字节、口令、DPAPI 明文结构；错误信息只允许出现 `key_id` 与错误类别。

---

## 4. 打开流程（fail-closed）

任何一步失败都必须**拒绝提供服务**，不得回退明文、不得自动重建空库：

| 步 | 动作 | 失败处理 |
|---|---|---|
| 1 | 校验 `JARVIS_DATA_MODE=synthetic`（当前阶段）与路径安全（非符号链接、父目录存在） | 拒绝启动，不创建任何文件 |
| 2 | 读 `meta.json`，校验 `format` 与 `cipher` | 未知版本 → 拒绝，提示升级路径 |
| 3 | 解包 `dek.dpapi`（用户范围 DPAPI，禁用交互 UI） | 解包失败 → 拒绝；**不**尝试恢复副本自动兜底 |
| 4 | 打开 SQLite3MC，设置 `cipher` + `key`，读一次页 1 | 错误密钥/损坏 → 拒绝，提示"密钥与数据不匹配" |
| 5 | `PRAGMA journal_mode=WAL`、`foreign_keys=1`、`temp_store=MEMORY` | 任一项不成立 → 拒绝 |
| 6 | 校验 `vault_meta.format` 行与 `key_id` 一致 | 不一致 → 拒绝（可能是旧备份或错配） |
| 7 | 迁移到当前 schema（事务内，失败整体回滚） | 失败 → 回滚并保留旧库 |
| 8 | `PRAGMA integrity_check` | 非 ok → 拒绝，指向备份恢复 |

**错误分类**（供 UI 与日志使用，不含敏感内容）：`KEY_MISSING`、`KEY_UNWRAP_FAILED`、`KEY_MISMATCH`、`KEYWRAP_MISSING`、`PASSPHRASE_REJECTED`、`KDF_PARAMS_REJECTED`、`FORMAT_UNKNOWN`、`DB_CORRUPT`、`MIGRATION_FAILED`、`DRIVER_UNAVAILABLE`。

---

## 5. 生命周期操作

### 5.1 创建（create）

前置：目标目录不存在或为空。步骤：生成随机 DEK → DPAPI 包装 → 在**暂存目录**建库、应用迁移、写入 `vault_meta` → 校验密文与可解锁 → `rename` 到目标 → 写 `meta.json` 与审计。任一步失败：删除暂存目录，目标不存在（与 `verify_apswmc_vault.py` 已验证行为一致）。

### 5.2 解锁（unlock）与锁定（lock）

- 解锁按第 4 节执行；解锁后仅向业务层提供连接，**不**把 DEK 暴露给路由或服务层。
- 进程退出即视为锁定；不提供"长期后台常驻解锁"。
- 内存中的 DEK 使用后立即释放引用；不写入环境变量、配置文件或崩溃转储可读的全局变量（Python 内无法保证彻底擦除内存副本，此限制写入威胁模型）。

### 5.3 轮换（rotate）

前置：库已解锁、无其他写连接、最近备份存在且可验证。

1. 生成新 DEK（新 `key_id`）并 DPAPI 包装到临时文件；
2. 以旧密钥打开，`PRAGMA rekey` 改为新密钥（独占，失败立即回滚并保留旧包装）；
3. 关闭后用**新**密钥重开验证：`integrity_check=ok`、`vault_meta.key_id` 已更新；
4. 用**旧**密钥尝试打开必须失败（验证旧密钥确实失效）；
5. 原子替换 `dek.dpapi`，旧包装移入 `retired/`，写审计 `key_rotated`。

失败语义：任一步失败 → 恢复旧包装、保留旧库、报告 `ROTATE_FAILED`；不得出现"库已用新密钥、包装还是旧密钥"的中间态。`rekey` 期间进程被杀死 → 下次打开必须失败并给出明确恢复指引（用备份 + 恢复材料），不得静默尝试猜测。

**已实现（S3，2026-11-07）：** 驱动确认支持 `PRAGMA rekey = raw:<hex>`（新密钥可读、旧密钥 `NotADBError`）。实际流程比草案更保守：先写 `dek.next.dpapi` 与 `meta.next.json`，再写 `pending.json` 标记，然后 rekey 并更新 `vault_meta.key_id`；成功后双向验证（新密钥必须能开、旧密钥必须不能开），再把旧包装移入 `retired/dek.<old_id>.dpapi`、原子换入新文件、删除标记。失败会尝试把库 rekey 回旧密钥并清理标记；进程在 rekey 之后被杀死时，下次打开由 `resume_rotation` 依据标记与 `dek.next.dpapi` **自动完成**轮换（这不是猜密钥，而是完成已记录的迁移），无法完成则清理标记回到旧密钥。**一键轮换与到期提醒（D5，2026-11-08）：** `backup_cli.py rotate --vault <DIR> --local <DIR>` 会先做一次安全备份（可选 `--remote` 同时上传），再执行轮换，并写审计行 `key_rotated`（含旧/新 `key_id` 与安全备份文件名）。`backup_cli.py status --vault <DIR>` 按 `rotated_at` 显示密钥年龄与到期日（默认 180 天，`ROTATION_INTERVAL_DAYS`），到期时提示运行 rotate。**计划轮换（定时自动执行）仍为未来项，本次不实现**，只保留手动触发 + 到期提醒这一中间档。

### 5.4 销毁（destroy）

前置：用户二次确认（输入 vault 名称）。步骤：删除 `dek.dpapi`、`meta.json`、`vault.db`、`-wal`、`-shm`、`retired/`，写审计 `vault_destroyed`。**不得**承诺物理擦除；文档与 UI 必须写明"旧备份与介质副本不在可控范围"。

---

## 6. 备份

### 6.1 备份单元（自包含归档）

一次备份 = **一个自包含归档** `jarvis-backup-<UTC 时间戳>-<key_id>.jvault`，必须同时包含三样东西，缺一不可恢复：

| 内容 | 说明 | 是否含密钥材料 |
|---|---|---|
| `vault.db` | 用**该次备份的随机密钥 BK** 加密的数据库快照 | 否，只有密文 |
| `key.bkwrap` | BK 的**两份**包装：口令派生 KEK 包装 + 恢复码包装 | 是**包装后**的密钥，不是明文 |
| `manifest.json` | 版本、key_id、哈希、KDF 参数、HMAC | 否 |

**关键修正（2026-11-07，修复恢复缺口）**：备份密钥的**包装数据必须随包走**。原设计"远端只放 `vault.db` 与 `manifest`"会导致原电脑丢失后，用户即使记得口令也没有可解开 BK 的材料——异地副本等于不可恢复。该写法已废止。

明确**不**随包走：明文 DEK/BK、口令、恢复码、**vault 自身的 DPAPI 包装**（绑定本机用户，异地无意义，且泄露结构信息）。

- 快照：用 SQLite 在线备份 API（或 `VACUUM INTO`）在**一个读事务内**完成，避免与并发写产生撕裂快照；
- 备份目标使用独立随机密钥（已验证：源密钥与标准 SQLite 都无法读取备份目标），因此备份泄露不等于主库泄露；
- 归档先在暂存目录生成、校验哈希后再 `rename`/上传，任何一步失败都不留下"看似完整"的半成品。

### 6.2 清单（manifest）字段

```json
{
  "format": "jarvis-backup-v1",
  "created_at": "2026-11-07T10:00:00Z",
  "source_key_id": "9f3c1a70b2d84e51",
  "backup_key_id": "3b77e0c4d19a25fe",
  "schema_version": 17,
  "tables": 24,
  "vault_db_sha256": "…",
  "archive_sha256": "…",
  "manifest_hmac": "…",
  "key_wraps": [
    {"type": "passphrase", "kdf": {"name": "argon2id", "m": 65536, "t": 3, "p": 2}, "salt": "…"},
    {"type": "recovery_code", "kdf": {"name": "argon2id", "m": 65536, "t": 3, "p": 2}, "salt": "…"}
  ]
}
```

- `vault_db_sha256` / `archive_sha256` 校验意外损坏；**不**防恶意篡改。
- `manifest_hmac`：用独立于库密钥的清单密钥（随恢复材料保管），防止他人替换清单与库。
- `key_wraps` 列出可用的解包路径；恢复时**任选其一**（口令或恢复码）。
- `recovery.mode` 已并入 `key_wraps`：只有 `machine` 包装（无 `key_wraps`）的备份明确标注"不可换机"。
- 解包时**必须**按清单参数执行 KDF，并拒绝参数下调（见 §9 B9）。

### 6.3 备份流程

1. 校验解锁状态与最近一次轮换状态；
2. 生成备份专用密钥 BK，打开目标库；
3. 在线备份复制页面 → 关闭目标；
4. **用口令派生 KEK 与恢复码 KEK 分别包装 BK**，写成 `key.bkwrap`（两份包装，均为密文）；
5. 计算 `vault_db_sha256` 与 `archive_sha256`，写 `manifest.json`（先写临时文件、`fsync` 后 `rename`）；
6. 用清单密钥对清单做 HMAC → 打包为 `.jvault`（zstd）→ 校验归档哈希；
7. 写审计 `backup_created`（记录 `key_id`、表数、耗时、目的地，不记录内容）。

**禁止**：备份过程中把明文写到任何中间文件；禁止在源库上留下未清理的临时文件；禁止把备份写进 `vault/` 目录；禁止把口令、恢复码或明文密钥写进归档、日志或清单。

### 6.4 恢复流程

1. 校验 manifest 的 `format` 与 HMAC；
2. 校验 `vault_db_sha256` 与文件实际哈希；
3. 从归档读 `key.bkwrap`，用**口令或恢复码**解包出备份密钥（KDF 参数按清单执行，且不得低于下限）→ 打开备份库 → `integrity_check`；
4. 迁移到当前 schema（若备份 schema 更旧）；
5. 写入**新的目标目录**（暂存 + 校验 + `rename`），绝不原地覆盖现有 vault；
6. 恢复后首次启动必须重新生成 DPAPI 包装（换机场景），写审计 `vault_restored`。

失败：删除暂存目录并保留原 vault 不变；报告 `RESTORE_REJECTED` 与具体失败项。

### 6.5 备份目的地

**已定（D3）**：本地与异地各保留一份；**默认云端为 S3 兼容对象存储**，GitHub Release 作为零成本替代；本地暂时没有外置盘，先落在本地目录，**如实标注其不安全边界**。

**允许上传**：归档 `jarvis-backup-*.jvault`（密文库 + **包装后的备份密钥** `key.bkwrap` + 清单）。
**严禁上传**：口令、恢复码、明文密钥、明文导出、vault 的 DPAPI 包装、`meta.json` 里的本机路径。

| 目的地 | 角色 | 频率 | 保留 | 关键约束 |
|---|---|---|---|---|
| 本地目录（当前无外置盘） | 本机副本 | 每日 | 最近 7 份 | **同盘不防磁盘故障/勒索软件**；仅为误删/误改回滚 |
| S3 兼容对象存储（默认） | 异地副本 | 每周 | 最近 4 份 | 开版本控制；Object Lock 30 天；生命周期清理；客户端加密后才上传 |
| 外置盘（半月后再加） | 离线副本 | 每周（插上时） | 最近 8 份 | 离线介质，防勒索软件；届时替代或补充本地目录 |
| GitHub Release（可选） | 免费异地档 | 每周或每月 | 最近 7 份 | 历史永久、无 Object Lock、单资产 2 GiB |

| 维度 | 结论与做法 |
|---|---|
| 上传内容 | 归档整体上传；**包装密钥必须在内**，否则异地恢复不可能（本次修正）。实测：`backups/` 前缀的桶锁对覆盖与删除都返回 `ObjectLockedByBucketPolicy` |
| 离线爆破 | 上传 `key.bkwrap` 后，攻击者可离线尝试口令。实测本机 Argon2id（m=64 MiB, t=3, p=2）约 **174 ms/次（≈5.8 次/秒/核）**；8 位常见词口令在 1000 核下约 **3 分钟**破解，8 位随机字符约 1.2×10⁶ 年/核。因此**口令要求提高到 12 位**并拒绝常见口令（见 §7） |
| 可见性 | 云端只接触密文；备份时间、体积、频率等元数据可见。不宣称"服务端什么都不知道" |
| 持久性 | 对象存储版本控制与 Git 历史都不易真正删除；任何上传都按"永久留存"对待。怀疑恢复材料泄露时**更换备份密钥**并停用旧归档 |
| 增长 | 每周一份整库副本；用压缩（zstd）与保留策略控制体积；接近上限前分卷 |
| 凭据 | S3 用最小权限 AK/SK（仅目标桶 PutObject/ListBucket，禁止 Delete 可选）；GitHub 用细粒度 PAT（仅该仓 `contents:write`）；一律存操作系统凭据管理器 |
| 可用性 | 账号/桶丢失等于备份丢失：本地副本 + 定期恢复演练（B7） |
| 上传前守卫 | 拒绝包含恢复材料或 DPAPI 包装的目录；校验归档与清单哈希一致才上传（B6） |
| 合规 | 遵守服务商条款与所在地数据要求；不适合时改用自控介质 |

### 6.6 备份计划（已实现）

```
每日 02:00  → backup_job.run：本地目录快照（保留 7 份）+ 追加审计行
每周日      → backup_job.run(sink=S3)：上传 .jvault（保留 4 份，锁定的对象跳过并记录）
每月一次    → 恢复演练：从本地或云端副本恢复到临时目录，跑 integrity_check 与业务自检（B7）
每半年或泄密疑点 → rotate_vault 换数据密钥；旧归档按保留期自然过期
```

**口令与恢复码的存放（已定）：** 不再使用环境变量，改为 **Windows 凭据管理器**（`Jarvis:backup-passphrase` 与 `Jarvis:backup-recovery-code`，generic credential、当前用户）。首次用 `python backend/experimental/backup_cli.py store-passphrase` 录入；恢复码由第一次备份自动生成并写入凭据管理器，用户应执行 `show-recovery-code` 抄写一份离线保存。

**实现要点（`experimental/backup_job.py`）：**

- 口令解析顺序：显式参数 → `JARVIS_BACKUP_PASSPHRASE`（应急）→ 凭据管理器；都没有时抛 `PASSPHRASE_UNAVAILABLE`，绝不猜；
- 恢复码同理，且**复用同一枚**，保证每次备份的归档都能被同一份材料打开；
- 保留策略：本地按修改时间保留最近 N 份；远端按列表顺序保留 N 份，遇到 `ObjectLockedByBucketPolicy` 记入 `remote_locked` 而不是失败；
- 审计：`<vault>/audit.log` 追加 JSON 行（`backup_created` / `backup_uploaded`），只记录归档名、`key_id`、字节数、保留统计，**不记录任何口令、恢复码或密钥**；
- 命令行：`store-passphrase` / `show-recovery-code` / `run --vault DIR --local DIR [--remote]`。

本地目录阶段明确接受的风险：磁盘损坏、误删、勒索软件会同时影响主库与本地副本；**缓解只能靠每周异地副本**，并在拿到外置盘后立刻补上离线副本。

---

## 7. 换机恢复材料

| 方案 | 可换机 | 日常体验 | 风险 | 采用 |
|---|---|---|---|---|
| 仅 DPAPI | ❌ | 无需口令 | 换机/重装即永久丢失 | 只作本机便利路径 |
| 口令派生 KEK（Argon2id） | ✅ | 备份时设置一次口令 | 口令弱则离线爆破 | ✅ 采用 |
| 一次性恢复码（高熵） | ✅ | 需抄写保存 | 丢失即不可恢复 | ✅ 同时提供 |
| 云端托管密钥 | ✅ | 最省事 | 与本地优先/服务端不可读冲突 | ❌ 不做 |

**已定（D1 两种都做）**：每次备份同时生成**口令派生包装**与**一次性恢复码**，两者都能解包同一份备份密钥，用户保存其中任一种即可恢复。UI 必须明确：**两种都丢失 = 备份不可恢复**，且不提供口令重置或客服找回。

**已改（D2 提高到 12 位，并做熵检查）**：包装数据会随备份上传，攻击者可离线爆破，因此 8 位不再接受。

| 攻击面 | 实测（本机 i5-1145G7，`bench_argon2_cost.py`） |
|---|---|
| Argon2id `m=64 MiB, t=3, p=2` | 约 **174 ms/次**，即 **≈5.8 次/秒/核** |
| 8 位常见词口令（10⁶ 词表） | 1 核约 2 天；1000 核约 **3 分钟** |
| 8 位随机字符（62 字母表） | 1 核约 1.2×10⁶ 年 |
| 12 位随机字符 | 1 核约 1.8×10¹³ 年 |
| 128 位恢复码 | 计算上不可行 |

规则：**口令最少 12 个字符**，创建时对照常见口令表（top 10k）拒绝；UI 同时提供"随机生成恢复码"作为默认推荐路径（恢复码 ≥128 位熵，用 base32 分组便于抄写）。KDF 参数写入清单并在解包时**强制不低于最低值**，禁止降到弱参数（B9）。密码学实现只从受支持的库中取，不自创协议。

恢复材料（口令、恢复码）**永不**进入备份包、仓库或任何自动同步目录；只由用户手工保管（纸质/密码管理器）。备份包内只出现**包装后的**密钥。

---

## 8. 崩溃、并发与损坏

| 场景 | 设计口径 | 当前证据 |
|---|---|---|
| 写入中被终止 | WAL 回滚未提交事务，已提交保留 | `verify_apswmc_crash_probe.py` 通过 |
| 备份中断 | 预存在的临时目标不被破坏；正式目标只在完成时出现 | `verify_apswmc_backup_probe.py` 通过 |
| 轮换中断 | 下次打开必须报 `KEY_MISMATCH`/`ROTATE_INCOMPLETE`，不得猜测密钥 | 待实现 |
| 页面损坏 | 打开时 `integrity_check` 拒绝；单页篡改应被 HMAC 拒绝 | 单页篡改验证通过 |
| 并发读写 | 多连接 WAL；写者 `busy_timeout`（5000 ms，实测确实等待）；`BEGIN IMMEDIATE` 前释放旧语句，避免 `SQLITE_BUSY_SNAPSHOT` 立即失败 | 160 次并发自增无丢失，读者单调可见（最高 160） |
| 并发压力 | N 写 × M 读 × T 秒，无丢更新、无死锁、无静默错误 | 待实现（S4） |

---

## 9. 验收矩阵

每个操作都要有"可执行、可重复、失败即阻塞"的检查，脚本命名沿用现有约定：

| 编号 | 验收项 | 断言 | 目标脚本 |
|---|---|---|---|
| K1 | 创建后可用 | 新密钥可读、`vault_meta.format` 正确、目录不含明文 | `verify_vault_key_lifecycle.py` |
| K2 | 缺密钥/缺包装 | 报 `KEY_MISSING`/`KEY_UNWRAP_FAILED`，不创建文件、不回落明文 | 同上 |
| K3 | 错误密钥 | 打开失败且错误分类为 `KEY_MISMATCH` | 同上 |
| K4 | 轮换成功 | 新密钥可读、旧密钥必失败、`key_id` 更新、审计有记录 | `verify_vault_rotation.py` |
| K5 | 轮换失败回滚 | 包装与库保持一致（都旧或都新），无中间态 | 同上 |
| K6 | 销毁 | 删除后无法打开，`meta.json` 不残留 key_id | `verify_vault_key_lifecycle.py` |
| B1 | 备份-恢复往返 | 备份后删除原库，恢复后业务数据一致 | `verify_vault_backup_restore.py` |
| B2 | 备份独立密钥 | 源密钥与标准 SQLite 均不能打开备份 | 同上 |
| B3 | 篡改检测 | 改 1 字节 → 拒绝；改清单 → HMAC 拒绝 | 同上 |
| B4 | 清单错配 | `source_key_id` 不符 → 拒绝 | 同上 |
| B5 | 备份期间并发写 | 快照一致（备份内的行数与事务边界自洽） | 同上 |
| B6 | 上传前守卫 | 目录含恢复材料/DPAPI 包装 → 拒绝上传；清单哈希不符 → 拒绝 | `verify_vault_backup_upload.py` |
| B7 | 远端恢复演练 | 仅用远端副本恢复 → 数据一致、迁移成功、恢复材料必需 | 同上 |
| B8 | 离线爆破上限 | 实测 KDF 成本并写入文档；12 位随机口令/128 位恢复码在实测速率下不可行；常见口令表命中即拒绝 | `bench_argon2_cost.py` + `verify_vault_backup_restore.py` |
| B9 | KDF 参数降级 | 清单参数低于最低值 → 拒绝解包（`KDF_PARAMS_REJECTED`） | 同上 |
| B10 | 包装缺失/损坏 | 归档缺 `key.bkwrap` 或包装损坏 → `KEYWRAP_MISSING` / `KEY_UNWRAP_FAILED`，不得尝试无密钥打开 | 同上 |
| C1 | 崩溃恢复 | 终止后已提交保留、未提交回滚、`integrity_check` ok | 已有 `verify_apswmc_crash_probe.py` |
| C2 | 轮换中断 | 重开报明确错误并指向恢复路径 | `verify_vault_rotation.py` |
| S1 | 并发压力 | ✅ 4×40 自增 160/160、读者单调、无死锁、无残留文件 | `verify_vault_concurrency.py` |

**2026-11-07 实测：** B5（备份期间并发写，快照 329 行全部成对一致）、B6（真实 R2 上传、上传守卫、桶锁拒绝覆盖与删除，错误码 ObjectLockedByBucketPolicy）、B7（从 R2 下载后分别用口令与恢复码恢复到新目录）均通过。B6/B7 需要凭据与网络，因此独立于离线套件运行；S1（并发压力）尚未实现。
| M1 | 加密库上迁移 | 旧 schema 备份 → 恢复 → 升级 → 回滚 | 复用 `verify_migrations.py`（已支持加密后端） |

---

## 10. 分阶段实施

| 阶段 | 交付 | 出口条件 |
|---|---|---|
| **S1 密钥与打开** | ✅ 已交付：`experimental/vault_keys.py`（create/open/destroy、meta、`key_id`、错误分类）+ `verify_vault_key_lifecycle.py`（K1–K3、K6 + 门禁守卫） | 打开流程可测；失败分类稳定（`KEY_MISSING` / `KEYWRAP_MISSING` / `KEY_UNWRAP_FAILED` / `KEY_MISMATCH` / `FORMAT_UNKNOWN` / `DB_CORRUPT` / `DRIVER_UNAVAILABLE`） |
| **S2 备份与恢复** | ✅ 已交付：`.jvault` 归档、口令/恢复码包装、KDF 下限、上传守卫、本地目录 sink、S3 sink（Cloudflare R2 实测）、远端恢复；`verify_vault_backup_restore.py` + `verify_vault_backup_upload.py` | B1–B10 全部通过；R2 实测 `ObjectLockedByBucketPolicy` 阻止覆盖与删除；保留策略、定时任务与远端清单巡检仍是后续工作 |
| **S3 轮换** | ✅ 已交付：`rotate_vault` / `resume_rotation`（pending 标记、双向验证、失败回滚、中断自动续做）+ `verify_vault_rotation.py`（K4/K5/C2） | 新密钥可读、旧包装必失效、失败后包装与 `key_id` 保持一致、中断后下次打开自动完成 |
| **S4 并发与压力** | ✅ 已交付：`verify_vault_concurrency.py`（4 写 × 40 次 + 3 读并发，实测 160/160 无丢失、读者观测到 160、无死锁、无残留 WAL） | 压力下无丢更新、无死锁、快照可见 |
| **自动化** | ✅ 已交付：`backup_job.py`（日程任务入口）、`wincred.py`（凭据管理器）、`backup_cli.py`（store-passphrase / show-recovery-code / run）、`tools/backup/schedule-backup.ps1`、`verify_backup_job.py` | 口令来自凭据管理器、保留策略生效、审计不含密钥、锁定对象只记录不失败 |
| **S5 切换准备** | 迁移到默认存储的切换脚本、回滚演练、许可证与发布审查清单 | 独立安全评审通过后才允许真实数据 |

**跨阶段约束**：S5 之前，任何阶段都不得把 `JARVIS_DATA_MODE` 的 synthetic 门禁降级，也不得把加密后端设为默认。

---

## 11. 决策记录

| 编号 | 问题 | 决定 |
|---|---|---|
| D1 | 恢复材料用口令、恢复码，还是都提供？ | ✅ **两者都提供**：每次备份同时生成口令派生包装与一次性恢复码，保存任一种即可恢复；UI 明示"两者都丢失 = 不可恢复" |
| D2 | 口令强度如何约束？ | ✅ **最小 12 个字符**（原定 8 位已废止：包装数据随备份上传，可离线爆破）；强制 Argon2id `m=64 MiB,t=3,p=2`，创建时拒绝常见口令，默认推荐随机恢复码（≥128 位熵）；实测见 §7 |
| D3 | 备份放哪？ | ✅ **本地 + 异地各一份**：每日本地目录（7 份；当前无外置盘，明确不防磁盘故障/勒索软件）、每周 S3 兼容对象存储（4 份，开版本控制与 Object Lock）、外置盘到手后补离线副本；GitHub Release 作零成本替代。归档含包装密钥，口令/恢复码永不随包（见 §6.5） |
| D4 | 是否为清单单独生成 HMAC 密钥？ | ✅ **做独立密钥**：每次备份生成随机清单密钥 MK，用同一口令/恢复码分别包装进 `key_wraps` 旁的 `manifest_wraps`；清单标记 `hmac_mode: independent`。实测：只拿到备份密钥无法伪造清单（`D4_BACKUP_KEY_ALONE_CANNOT_FORGE_MANIFEST`）；旧归档仍按 `derived` 模式可读 |
| D5 | 轮换是否需要"计划轮换 + 手动轮换"两档？ | ✅ **只做手动 + 到期提醒**：`rotated_at` 起 180 天到期（`rotation_status`），`backup_cli.py status` 显示年龄/到期日，`backup_cli.py rotate` 先自动备份再轮换并写 `key_rotated` 审计。**计划轮换（定时自动执行）已记录为未来项，暂不实现** |
| D6 | 当前加密驱动的原语和依赖能否证实，是否可承载真实数据？ | ⏳ **当前构建不得承载真实数据**，但不能把 SQLCipher 候选的 OpenSSL 3.6 结论归于 APSW/SQLite3MC。当前 SQLite3MC 源码检查未发现 OpenSSL/Botan 引用，现有 wheel 的编译来源与依赖仍未证实；先完成来源、许可证、构建记录及依赖审查（G1），再决定自建同路线或更换驱动。只有实际依赖 OpenSSL 的候选才需要评估其版本与维护状态。见 [风险整改方案](RISK_REMEDIATION_OPTIONS.md)。 |

---

## 12. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| DPAPI 绑定用户/机器 | 换机无法解密 | 强制推荐可换机备份模式；UI 不承诺找回 |
| 用户遗忘口令 | 数据不可恢复 | 备份时二次确认；不提供"重置"；文档写明 |
| 轮换半途中断 | 库与包装不匹配 | 先备份、写入新包装前校验、失败回滚、错误分类 |
| 备份被一起偷走 | 等同于库泄露 | 备份独立密钥 + 恢复材料分离保管 |
| 上传的包装数据可离线爆破 | 口令弱则备份可被解开 | 口令 ≥12 位 + 常见口令表拒绝 + Argon2id 强参数；默认推荐随机恢复码；参数不可降级（B9） |
| 本地副本与主库同盘 | 磁盘故障/勒索软件同时损坏 | 明确标注；每周异地副本兜底；外置盘到手后补离线副本 |
| 驱动来源与依赖未证实 | 长期安全风险 | 当前 APSW/SQLite3MC wheel 仅用于合成数据；完成 G1 的构建、组件与依赖证据后再评估真实数据。OpenSSL 3.6 的已知问题只适用于已测 SQLCipher 候选，不能外推到本驱动。 |
| 内存中密钥副本 | 同用户恶意程序可读 | 列入威胁模型，不宣称防御 |
| 删除不等于擦除 | 旧备份/介质残留 | 文档与 UI 明示 |

---

## 13. 与现有实现的对齐

| 已有 | 本设计如何使用 |
|---|---|
| `experimental/vault_keys.py` | S1 已实现：create/open/destroy、`meta.json`、`key_id`、错误分类；S3 轮换 `rotate_vault` / `resume_rotation` |
| `experimental/vault_backup.py` | S2 已实现：归档、包装、清单 HMAC、恢复、上传守卫 |
| `experimental/backup_sink.py` | S2 已实现：本地目录与 S3（R2 实测）两个目的地 |
| `experimental/backup_job.py` / `wincred.py` / `backup_cli.py` | 自动化：日程任务、Windows 凭据管理器、命令行入口 |
| `verify_backup_job.py` | 自动化验收：凭据往返、保留策略、审计不含密钥、锁定容忍、两种材料都能恢复 |
| `verify_vault_key_lifecycle.py` / `verify_vault_rotation.py` | S1 验收：K1–K3、K6；S3 验收：K4、K5、C2 |
| `verify_vault_backup_restore.py` / `verify_vault_backup_upload.py` | S2 验收：B1–B5、B8–B10（离线）+ B6/B7（真实 R2） |
| `experimental/apswmc_storage.py` | S1 的连接层；打开时按 §4 校验 cipher/key 与页 1 |
| `experimental/apswmc_synthetic_vault.py` | 初始化/暂存/rename/DPAPI 包装的既有模式直接复用 |
| `experimental/windows_dpapi.py` | 用户范围 DPAPI 包装/解包（含附加熵与内存清理） |
| `verify_apswmc_vault.py` / `_backup_probe.py` / `_crash_probe.py` | 作为 B1、C1 的起点，扩展成验收矩阵 |
| `app/database.py` 的适配器开关 | S1–S4 继续只走实验开关；S5 才讨论默认切换 |
| `verify_migrations.py` | 已可在加密后端运行，直接复用 M1 |
