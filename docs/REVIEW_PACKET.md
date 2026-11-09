# 安全评审包（Review Packet）

**版本：** 2026-11-08  
**对象：** Jarvis 实验加密存储（vault：密钥生命周期 + 备份恢复 + 轮换 + 并发）  
**状态：** 默认存储**未**切换；真实个人数据仍禁止进入。本文供 G2 独立评审使用。  
**配套文档：** [数据保护方案](DATA_SECURITY_DESIGN.md)、[密钥生命周期与备份](KEY_LIFECYCLE_AND_RECOVERY.md)、[切换计划](STORAGE_SWITCH_PLAN.md)、[存储适配计划](STORAGE_ADAPTER_PLAN.md)

---

## 1. 怎么用这份包

评审者按 §7 的证据表逐项核对代码与命令输出即可复核每一条声明。凡是"未验证"的项都在 §9 明示，不在正文里用"应该没问题"含糊过去。评审结论请直接回答 §12 的 12 个问题。

## 2. 系统边界与数据流

```
用户操作 → 本地 FastAPI（127.0.0.1:8765，Bearer + Host/Origin 守卫）
        → 领域服务（todos / mail / knowledge / conversations / approvals / audit）
        → database.connect()
             ├─ 默认：sqlite3 明文 + JARVIS_DATA_MODE=synthetic 门禁
             └─ 实验：APSW + SQLite3 Multiple Ciphers（chacha20，WAL）
                    └─ vault：vault.db + dek.dpapi + meta.json + audit.log
备份：vault → .jvault（独立备份密钥）→ 本地目录 / Cloudflare R2（桶锁 35 天）
```

**不进入本设计范围：** 模型供应商调用、Web 端、跨设备同步协议、账号 OAuth。这些各有独立验收门槛。

## 3. 威胁模型

| 威胁 | 是否覆盖 | 说明 |
|---|---|---|
| 磁盘/文件被复制、离线读取 | ✅ | 库、WAL、备份均为密文（chacha20 / 独立备份密钥） |
| 备份文件泄露 | ✅ | 备份用独立随机密钥；口令/恢复码不随包 |
| 备份被篡改或替换 | ✅ | 库哈希 + 独立清单密钥 HMAC（D4）；篡改必拒 |
| 换机/重装 | ✅ 有条件 | 需口令或恢复码；DPAPI 单独不可迁移 |
| 进程崩溃/断电 | ⚠️ 部分 | WAL 回滚已验证；未做真实断电测试 |
| 备份中途中断 | ✅ | 临时目标不被破坏，正式目标只在完成时出现 |
| 同用户恶意程序、内存转储、键盘记录 | ❌ | 明文密钥必然存在于进程内存；不宣称防御 |
| 管理员级攻击、内核级 Rootkit | ❌ | 超出应用层能力 |
| 已泄露的旧备份 | ❌ 不可撤回 | 只能换钥并停用旧归档；删除不等于擦除 |
| 用户遗忘口令与恢复码 | ❌ 不可恢复 | 设计上不提供重置或客服找回 |

## 4. 密钥与打开流程

- **层级：** 每 vault 一个 32 字节随机 DEK；`dek.dpapi` 用当前用户范围 DPAPI 包装（附加熵、禁止交互 UI、使用后清零缓冲区）。
- **文件：** `meta.json`（format / key_id / created_at / rotated_at / cipher）、`dek.dpapi`、`vault.db`、`audit.log`、`retired/`。
- **打开 8 步（全部失败关闭）：** 门禁 → 路径/符号链接检查 → `meta.json` 校验 → DPAPI 解包 → 密钥打开并读页 1 → `vault_meta.format`/`key_id` 比对 → `integrity_check` → 提供连接。
- **错误分类：** `KEY_MISSING`、`KEYWRAP_MISSING`、`KEY_UNWRAP_FAILED`、`KEY_MISMATCH`、`FORMAT_UNKNOWN`、`DB_CORRUPT`、`DRIVER_UNAVAILABLE`、`ROTATE_FAILED`。分类之外的异常一律上抛，不吞。
- **轮换：** `PRAGMA rekey`，先写 `dek.next.dpapi` + `meta.next.json` + `pending.json`，成功后双向验证（新钥可开、旧钥必失败），旧包装退役到 `retired/dek.<old_id>.dpapi`；失败尝试回滚；中断由 `resume_rotation` 完成或放弃。

## 5. 备份、恢复与轮换

| 项 | 设计 |
|---|---|
| 归档 | `.jvault`（zip）：`vault.db`（一次性随机 BK 重新加密）+ `key.bkwrap` + `manifest.json` |
| 材料 | 口令（Argon2id，`m=64 MiB,t=3,p=2`，≥12 字符 + 常见口令拒绝）与 160 位恢复码，两路等价 |
| 清单 | `db_sha256` + 独立清单密钥 HMAC（`hmac_mode: independent`），KDF 参数不可降级 |
| 恢复 | 新目录暂存 → 哈希/HMAC → 解包 → `integrity_check` → `key_id` 比对 → DPAPI 重包 → 原子 rename |
| 目的地 | 每日本地目录（7 份）；每周 R2（4 份，`backups/` 前缀桶锁 35 天） |
| 凭证存放 | Windows 凭据管理器（`Jarvis:backup-passphrase`、`Jarvis:backup-recovery-code`） |
| 审计 | `<vault>/audit.log` 追加 JSON 行，只含归档名/`key_id`/字节数/保留统计 |

## 6. 加密实现与依赖

| 项 | 现状 |
|---|---|
| 驱动 | `apsw-sqlite3mc`（APSW 3.54.0.0 + SQLite3 Multiple Ciphers 2.6.1），cp314 Windows wheel |
| 算法 | ChaCha20 页加密，raw key（32 字节 hex），WAL |
| 来源 | PyPI 预构建 wheel；未自行构建，未做可复现构建对照。哈希记录在 `docs/driver-manifest.json`，`verify_driver_provenance.py` 检测漂移 |
| 依赖风险 | 当前 APSW/SQLite3MC wheel 的原语来源与构建依赖尚未证实，不能据此声称随包使用 OpenSSL 3.6。已测 SQLCipher 候选另有 OpenSSL 3.6 维护风险；两条路线须分别评审。G1 未通过，真实数据仍禁止。 |
| 许可证 | 随包只有 APSW 的 zlib 风格文本（`dist-info/licenses/LICENSE`）；SQLite3MC(MIT) 与 SQLite(public domain) 文本缺失 → 分发前必须补齐（实测） |
| 加载方式 | 仅显式 `JARVIS_STORAGE_ADAPTER=apswmc` + 64 hex 密钥时导入；默认路径零依赖 |

## 7. 验收证据

运行环境：项目便携 CPython 3.14 + `.jarvis-python314-deps` + `.jarvis-python314-apswmc-probe`。  
命令：`& <repo>\.jarvis-python-3.14.10\python.exe backend\verification\verify_all.py`

| 编号 | 验收点 | 脚本 | 结果 |
|---|---|---|---|
| K1–K3, K6 | 创建/打开/错误密钥/销毁 | `verify_vault_key_lifecycle.py` | PASS |
| K4, K5, C2 | 轮换、失败回滚、中断续做 | `verify_vault_rotation.py` | PASS |
| B1–B5, B8–B10 | 归档、恢复、篡改、KDF、包装缺失、并发快照 | `verify_vault_backup_restore.py` | PASS |
| D4 | 独立清单密钥不可被备份密钥伪造 | 同上 | PASS |
| B6, B7 | 真实 R2 上传守卫、桶锁、远端恢复 | `verify_vault_backup_upload.py` | PASS |
| 自动化 | 凭据管理器、保留策略、审计无密钥、锁定容忍 | `verify_backup_job.py` | PASS |
| G6 | 明文→vault 逐表复制 + 26 项校验 | `verify_migrate_to_vault.py` | PASS |
| G7 | 回滚演练（快照恢复可用） | 同上 | PASS |
| M1 | 加密库上的迁移与回滚 | `verify_migrations.py` | PASS |
| S1 | 并发压力（4 写 × 40 + 3 读） | `verify_vault_concurrency.py` | PASS |
| 应用 schema | 17 迁移 / 32 表住在 vault 内 | `verify_vault_app_schema.py` | PASS |
| 全套 | 默认与加密后端逐项一致 | `verify_all.py` / `verify_adapter_suite.py` | **56 PASS / 0 FAIL / 0 SKIP** |

提交对应：S1 `54248f1`、S2 `1031727`、S3 `d1c2c8e`、S4 `55987cd`、自动化 `cc5c38a`+`c47617f`、D4/D5/D6 `417b484`、G6/G7 见最新提交。

## 8. 已修复的缺陷（来自自测）

| # | 缺陷 | 影响 | 修法 |
|---|---|---|---|
| 1 | 游标集合强引用，一次性查询读快照不释放 | 同一连接永远读旧值（静默） | 改 `weakref.WeakSet` |
| 2 | `BEGIN IMMEDIATE` 前不释放旧语句 | 先读后写 20 ms 内 `BusyError` | 开事务前 finalize |
| 3 | `open_vault` 异常分类包住 `yield` | 调用方异常误报为 `DB_CORRUPT` | 手动驱动上下文管理器 |
| 4 | 归档名精确到秒 | 同秒两次备份撞名 | 重名追加后缀 |
| 5 | 应用迁移写进另一个 DB 文件 | vault 内出现两个库 | `JARVIS_VAULT_DIR` 指向 `vault.db` |

## 9. 已知限制与不承诺

- 内存中的明文密钥无法保证擦除；同用户恶意程序可读。
- DPAPI 绑定用户与机器；无口令/恢复码时换机不可恢复。
- 删除与销毁均**不承诺**物理擦除；旧备份与介质副本不在可控范围。
- 未做真实断电、硬件损坏、文件系统级损坏测试。
- 未做端到端性能基线（G5），未做 14 天稳定运行观察（G4）。
- 未做独立第三方评审（G2 即本次目标）。
- 备份对象在 R2 上受 35 天桶锁约束；保留 4 份每周备份意味着最早一份可能在锁定期内无法删除（已按"跳过并记录"处理）。

## 10. 未完成门槛

| 门槛 | 状态 |
|---|---|
| G1 驱动来源/许可证/构建审查 | 🟡 清单与检测就绪（`DRIVER_REVIEW_AND_BUILD.md`、`verify_driver_provenance.py`）；实测两条缺口：许可证文本仅 APSW、加密后端不可辨识；尚未完成来源到产物的构建证据 |
| G2 独立安全评审 | ⏳ 本文即为输入 |
| G4 自动化稳定运行 ≥14 天 | ❌ 未开始 |
| G5 端到端性能基线 | ❌ 未做 |

## 11. 复现方式

```powershell
$repo = 'C:\Users\Admin\.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db'
$py = "$repo\.jarvis-python-3.14.10\python.exe"
& $py "$repo\backend\verification\verify_all.py"              # 运行当次清单，记录数量与 SKIP
& $py "$repo\backend\verification\verify_adapter_suite.py"    # 同一套跑在加密后端
& $py "$repo\backend\verification\verify_vault_backup_upload.py"  # 需 R2 凭据
& $py "$repo\backend\verification\verify_migrate_to_vault.py"     # G6/G7
```

所有脚本只使用临时目录与虚构数据；`sources/` 与真实数据不参与。

## 12. 给评审者的 12 个问题

1. 打开流程的 8 步是否存在可绕过的路径？特别是符号链接、`meta.json` 与库 `key_id` 不一致、以及 `pending.json` 续做。
2. `dek.dpapi` 的附加熵与 DPAPI 用户范围是否足够，还是需要 DPAPI-NG/TPM？
3. 轮换的"先写 next 再 rekey"顺序是否留下不可恢复窗口？`resume_rotation` 的判定是否可能误判（把旧钥库当成新钥完成）？
4. 备份归档的 `key.bkwrap` 与 `manifest_wraps` 是否存在可离线爆破的额外面？
5. 独立清单密钥（D4）是否真正切断了"拿到备份密钥即可伪造清单"的路径？`derived` 兼容分支是否构成降级攻击面？
6. 口令 ≥12 位 + Argon2id 参数，在当前硬件下对"人类构造口令"的防护是否足够？
7. 恢复流程是否可能在目标目录留下半成品或被覆盖？
8. 桶锁 35 天 + 保留 4 份的组合是否会导致备份无法清理、成本失控？
9. `weakref` 修复后，是否存在"游标仍被引用导致快照长期持有"的残留路径（例如长迭代中写入）？
10. `JARVIS_VAULT_DIR` 的引入是否可能在未初始化目录上创建明文库？
11. 审计日志是否会泄露敏感元数据（归档名含 `key_id`、时间戳）？
12. 能否用可复核的构建记录与二进制依赖清单证明当前 APSW/SQLite3MC wheel 实际使用的加密原语及其来源？
