# Jarvis 本地数据保护与恢复方案 J3

**状态：设计提案；除合成数据门禁外均未实现。** 更新：2026-11-06。

## 当前事实和保护范围

当前使用 Python 标准库 SQLite，数据、索引和可能的临时文件均不能宣称已加密。数据库连接要求显式设置 JARVIS_DATA_MODE=synthetic；其他值和未设置都会在创建目录/文件前失败。这个开关只能阻止误把系统设成“个人数据模式”，不能识别合成文本中混入的真实内容，也不是加密或访问控制。真实邮件、聊天、密钥和个人资料暂不进入该数据库。

要保护的对象包括邮件正文和附件、收集与知识、待办、会话、索引、草稿、临时文件、备份、密钥和令牌。目标是在设备丢失或离线副本被复制时降低泄露，并让同步服务只接触内容密文。已解锁设备上的同用户恶意程序、被攻陷的客户端、用户主动提供给模型的明文，不在磁盘加密单独能解决的范围内。

## Windows 本地存储提案

1. 选用经过审查的全库加密 SQLite 实现，例如 SQLCipher；先验证 Python/Windows 兼容性、构建来源、许可证和维护方式，不能只对普通 sqlite3 执行 PRAGMA key 就宣称启用加密。SQLCipher 的密钥必须在首次访问数据库前设置，且普通 SQLite 明文库迁移需专门导出路径。[SQLCipher API](https://www.zetetic.net/sqlcipher/sqlcipher-api/)
2. 每个用户资料库生成随机数据密钥；Windows 端可评估用用户范围 DPAPI 包装本机密钥，避免把明文密钥写入配置、日志或环境变量。DPAPI 通常绑定 Windows 用户和计算机；机器范围标志会扩大解密范围。它不能单独解决换机与恢复。[CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
3. 检查数据库主文件、回滚日志、WAL、临时文件、索引、附件和备份。SQLCipher 文档说明 WAL 页面受密钥保护，并对临时存储构建配置提出条件；实际打包版本必须逐项验证，不能只看数据库扩展名。[SQLCipher 设计](https://www.zetetic.net/sqlcipher/design/)
4. 服务启动时先确认实际加密驱动、解锁结果与数据库版本；缺驱动、密钥不可用、错误密钥或不支持版本时拒绝访问，不回退到明文 SQLite。解锁后才开放本地业务接口。
5. 目前 bearer 环境变量只是开发门禁，不是完整登录或安全桌面会话；下一版需要本机可信会话、短期凭据、客户端绑定、Origin/Host 检查和操作授权，模型不得取得 API 令牌。

## 备份与恢复提案

- 恢复材料与数据密钥分开保管；可评估用户设置的恢复口令或恢复密钥包装随机数据密钥。具体算法、库、参数和恢复体验必须在实现前单独定稿并评审，不自创密码协议。
- 换机、Windows 账户损坏或丢失 DPAPI 材料时，只有用户妥善保存的恢复材料和可用备份才能恢复；没有恢复材料时，不承诺找回。
- 导出备份需包含加密数据与必要的加密密钥包装和版本信息；先在隔离位置验证完整性与可解锁性，恢复时先写到临时目标、验证后再由用户选择替换，并保留可回滚副本。禁止将密钥或未加密中间文件写入日志。
- 当前软删除是可恢复的状态变更，不是安全擦除。删除项可能仍存在备份、旧设备或存储介质；同步删除、备份保留和密钥轮换需明确策略，不能承诺立即抹除所有副本。

## 多设备与 Web

Windows 本地 DPAPI 保护和跨设备端到端加密是两件独立工作。同步协议应在客户端加密后上传，服务器仅保存密文和必要路由/版本元数据；设备加入、撤销、恢复、离线冲突和删除墓碑需单独设计并评审。被撤销设备已经复制的明文不能远程收回，后续访问需要轮换相关密钥。

Web 端需要在受信任的安全上下文里执行客户端加解密；浏览器脚本供应链和 XSS 是关键威胁。浏览器本地存储可能被清理或回收，不能将其当作唯一备份。[Web Crypto API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API)、[浏览器存储策略](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria)

## 实现前门槛与验收

- 固定并审查加密驱动的来源、版本、Windows 安装/构建方式与许可证；当前网络限制使依赖获取未完成。
- 使用合成数据验证：正确密钥可重启读取，错误密钥和普通 SQLite 无法读取；检查主库/WAL/临时目录/日志/备份不含已知明文；损坏数据能可靠报错。
- 测试首次初始化、锁定/解锁、换机恢复、丢失恢复材料、备份中断、迁移失败回滚、密钥轮换和撤销；覆盖崩溃与并发场景。
- 加密驱动、OS 密钥管理和恢复流程通过独立安全审查后，才解除真实个人资料禁用。跨设备同步、浏览器安全和模型传输另有各自验收门。

## 目前已实现

只有合成数据模式显式门禁和临时数据验证；没有 SQLCipher、DPAPI 密钥包装、加密数据库、备份恢复或 E2E 同步。


## Windows 驱动候选调研（2026-11-06）

候选为社区绑定 `sqlcipher3`，不是已选定或已安装的生产依赖。[PyPI 发布页](https://pypi.org/project/sqlcipher3/)列出 0.6.5 的 CPython 3.13 Windows x86-64 预构建包，文件名为 `sqlcipher3-0.6.5-cp313-cp313-win_amd64.whl`；同页标记该文件无 provenance，并列出 SHA-256 `c3b5f2afc45a64d987dde007c8ef7d21f47014dcf85633c29cccdd764cdedec3`。散列只能校验与来源所发布文件一致，不能独立证明发行者、构建链或代码可信。0.6.5 发布于 2026-11-05，仍需评估成熟度。不得通过变通方式绕过当前网络限制获取文件。

绑定项目的[许可证](https://github.com/coleifer/sqlcipher3/blob/master/LICENSE)和[构建配置](https://github.com/coleifer/sqlcipher3/blob/master/setup.py)可供后续审查；包内 SQLCipher、OpenSSL 等组件的实际版本、许可、编译选项和分发义务仍须针对取得的具体发行物核对，不能仅依据绑定许可证下结论。项目页面声称 0.6.2 起提供自包含 wheel，实际安全性和可加载性尚未在本机验证。

`backend/verification/verify_sqlcipher_candidate.py` 是独立的临时合成数据探针：检查 cipher_version、正确密钥重开、错误密钥和普通 SQLite 拒绝读取、WAL 与主库中已知标记未明文出现。依赖不存在时返回 NOT_RUN；`verify_sqlcipher_probe_guard.py` 已证实将普通 sqlite3 冒充驱动时探针拒绝通过。标记扫描只能发现该标记的明显泄露，不能证明所有内容、临时文件、备份、内存、日志或崩溃文件安全。SQLCipher 文档要求在首次访问数据库前设置密钥，错误密钥必须通过读取实际页面检测：[SQLCipher API](https://www.zetetic.net/sqlcipher/sqlcipher-api/)。

探针没有接入 Jarvis 生产存储；DPAPI、持久密钥、恢复、备份、断电恢复、安全审查及实际驱动测试均未完成，真实资料仍禁止导入。


## DPAPI 本机探针（2026-11-06）

`backend/verification/verify_windows_dpapi_candidate.py` 只在内存中生成随机测试字节，使用当前用户范围 DPAPI 并禁止交互窗口，计划检查解封、错误附加熵和内容篡改。探针不主动读取或存储 Jarvis 密钥，也未接入 Jarvis；DPAPI 本身依赖 Windows 用户密钥系统，可能产生其自身的系统状态。

在当前受限执行环境使用系统 Python 3.13 和内置 Python 3.12 运行，均在首次 `CryptProtectData` 返回 Windows 错误 2，未进入解封和篡改测试，结果为失败而非通过。原因尚未确定，不能断言是权限、用户配置或代码问题；需要在正常用户登录会话中进一步诊断。DPAPI 的用户/计算机绑定及附加熵行为参见 [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。探针内对部分原生缓冲区进行清零，但 Python 的不可变对象或系统内存副本无法由此保证擦除；不能把探针当作完整密钥管理实现。

补充诊断：分别不带附加熵并切换 UI 禁用标志，首次调用仍返回错误 2；当前进程的 APPDATA 路径不存在，将 APPDATA 临时指向隔离目录也未解决。以上不能确定根因，也不证明其他登录会话同样失败。


## 本机探针复测（2026-11-07）

用户协助创建的虚拟环境中检测到另一候选发行包 `sqlcipher3-wheels 0.5.7`，其内报告 SQLCipher 4.12.0 community、SQLite 3.51.1；它不是前文调研的 `sqlcipher3 0.6.5` 发行物。包自述为社区 fork、Beta，具体 wheel 来源、构建和全部第三方许可仍需审查，暂未选为正式依赖。

在正常权限的本机进程中，临时虚构数据库的正确密钥重开、错误密钥拒绝、普通 SQLite 拒绝和主库/WAL 测试标记扫描通过；Windows 用户范围 DPAPI 的随机数据加解封、错误附加熵和篡改拒绝也通过。此前受限环境返回系统错误 2 的现象仍保留为环境差异记录。

首次 SQLCipher 探针因脚本错误未正确插入测试密钥，错误密钥检查失败；修复后重新运行才通过。该探针未测试密钥恢复、备份、临时文件、崩溃恢复、数据库迁移或安全审查，且没有与 Jarvis 存储集成。当前 SQLite 数据库依旧明文，真实资料仍不可导入。


## 加密依赖维护状态（2026-11-07）

本机虚拟环境的 sqlcipher3-wheels 0.5.7 报告 SQLCipher 4.12.0、OpenSSL 3.6.0。另将 sqlcipher3 0.6.5 的 Windows CPython 3.13 wheel 按 PyPI 公布的 SHA-256 校验后安装到独立候选目录，不覆盖现有环境；该模块报告 SQLCipher 4.20.0，实际 provider 同样为 OpenSSL 3.6.0。校验散列只表明与所查发布文件一致，不验证构建过程和发行者。两份发行物均未成为生产依赖。

OpenSSL 官方说明 3.6 系列已于 2026-11-01 停止公开安全维护，并发布过多个 3.6.0 之后的安全修复；这些漏洞是否可经本实验的数据库调用触发尚未评估，不能直接推断已被利用。来源：[OpenSSL 3.6 停止维护通知](https://openssl-library.org/post/2026-11-01-eol34-36/)、[3.6 版本安全修复记录](https://mirror.openssl-library.org/news/openssl-3.6-notes/)。因此这两个构建仅用于虚构数据实验，不选作真实数据加密依赖。

同一 Python 3.13.12 解释器的 ssl 模块报告 OpenSSL 3.0.18；OpenSSL 3.0 系列已于 2026-09-07 停止公开安全维护：[官方通知](https://openssl-library.org/post/2026-09-16-eol30/)。当前真实账户与模型网络调用均未启用；正式联网前应迁移到受支持且更新的 Python/加密运行环境，并核验各二进制实际使用的版本。Python 3.13.16 官方发布说明记载 Windows 已切换到 OpenSSL 3.5 LTS，但 3.13 后续安全发布的 Windows 安装方式仍需评估：[Python 发布说明](https://www.python.org/downloads/release/python-31316/)、[Python 3.13.18 说明](https://www.python.org/downloads/release/python-31318/)。

后续优先评估来源可复核、持续维护且使用受支持 OpenSSL 的 SQLCipher 构建或自行构建；需复测数据库兼容、打包、更新、许可和恢复。不要将当前实验结果视为生产安全审计。


在 Python 3.14.10 下另行校验并隔离安装 sqlcipher3 0.6.5 的 CPython 3.14 wheel 后，先加载 Python 自带 ssl，再加载 SQLCipher：Python 报 OpenSSL 3.5.9，而该驱动仍报告 OpenSSL 3.6.0。不能通过仅升级 Python 假定数据库驱动也使用更新的加密库；该 wheel 继续只供合成实验。


进一步按发布哈希隔离验证 rotki-pysqlcipher3 2026.8.3 的 Windows CPython 3.14 wheel：PyPI 显示其具有 GitHub Actions 发布来源证明，但我们尚未独立验证证明链或审查源码；实际加载后报告 SQLCipher 4.12.0 和 OpenSSL 3.6.0，同样已停止公开维护，因此仍不适合作为真实资料的正式依赖。[该 wheel 的 PyPI 发布信息](https://pypi.org/project/rotki-pysqlcipher3/)。

## 另一加密候选的隔离探针（2026-11-07）

在项目独立目录按 PyPI SHA-256 固定安装 apsw-sqlite3mc 3.54.0.0 的 Windows CPython 3.14 wheel。PyPI 页面显示该文件具有由其验证的 GitHub Actions 发布证明；我们尚未独立验证证明链、逐项审阅构建和许可，也未将其纳入正式依赖。实验运行时报告 APSW 3.54.0.0、SQLite 3.54.0、SQLite3 Multiple Ciphers 2.6.1，编译选项报告 TEMP_STORE=2 与 SECURE_DELETE。项目文档将其描述为自包含的不同加密实现，不能推断它经过独立安全审计，也不能把它当成原先 SQLCipher 构建。来源：[发行物和来源信息](https://pypi.org/project/apsw-sqlite3mc/)、[组件说明](https://github.com/utelle/apsw-sqlite3mc)、[密码实现说明](https://utelle.github.io/SQLite3MultipleCiphers/docs/ciphers/cipher_overview/)。

用临时虚构数据库显式选择 chacha20，设置随机测试密钥并实际访问数据库：正确密钥跨进程重开，错误密钥和标准 SQLite 拒读，测试标记未在主库及非空 WAL 中出现，对一个数据页的单字节修改被拒绝。验证脚本为 backend/verification/verify_apswmc_candidate.py；这些有限测试不能证明所有损坏可检测，不能证明备份、崩溃恢复、全部临时文件或安全性。项目文档对临时文件的声明尚未由本项目独立验证。

初次运行因把 PRAGMA 设置返回值误当成读取结果而失败，随后因 sqlite3 上下文管理器未关闭连接导致临时目录清理失败；均已修复，指定的失败实验目录已清理，最终独立进程和清理验证通过。APSW 与标准 sqlite3 的接口、事务和行格式不同，不能只替换导入；必须先审查适配层和错误传播并重新测试全部业务。这个候选未连接主应用，无备份/恢复/密钥轮换，也未完成安全审查。当前主库仍明文，禁止输入真实资料。

进一步在临时 APSW 加密库中逐条试运行当前项目迁移 SQL，写入虚构待办、回滚修改、检查外键并重开，结果通过；这只是 SQL 兼容性实验，并未运行主应用的迁移入口或业务服务。实验还确认 APSW 返回 tuple 而非现有服务所依赖的 sqlite3.Row，且没有相同的 commit 方法，需先设计和验证适配层，不能直接替换数据库驱动。见 backend/verification/verify_apswmc_schema.py。

此外新增独立的 APSW SQLite3MC + Windows 当前用户 DPAPI 虚构库实验：随机密钥由 DPAPI 包装，本机进程重启后重新解封；初始化失败清理暂存目录、已有目标不覆盖、错误密钥和标准 SQLite 拒读、损坏包装密钥拒绝、事务异常回滚均已验证。实验与现有 SQLCipher 原型并列，均不连接主应用。见 backend/experimental/apswmc_synthetic_vault.py 和 backend/verification/verify_apswmc_vault.py。没有备份、换机恢复、设备同步、可信用户认证或安全评审，不具备真实数据使用条件；Python 进程内密钥复制也不能保证安全擦除。

备份机制探针：在临时虚构数据库、源 WAL 存在时，用 SQLite 在线备份将加密源复制到使用独立随机密钥的加密目标；目标关闭重开和完整性检查通过，源密钥和标准 SQLite 均不能读取目标。中断复制后，预先存在的临时目标内容得以保留。代码仅为 backend/verification/verify_apswmc_backup_probe.py，不是产品备份功能；未验证跨机器恢复、密钥封装、崩溃/断电、文件替换、所有临时文件或备份真实性。APSW 说明在线备份会复制数据库页面，完成后需结束备份对象：[APSW Backup](https://rogerbinns.github.io/apsw/backup.html)。

异常退出探针：仅终止专门创建的虚构测试子进程；子进程写入大量未提交记录后被终止，重新打开独立 APSW + DPAPI 实验库时已提交记录保留、未提交记录回滚、integrity_check 通过，非空 WAL 未发现指定明文标记。见 backend/verification/verify_apswmc_crash_probe.py。这不等于断电、硬件故障或所有写入时机的恢复验证，更不证明真实备份可用。

候选许可初查：该 wheel 的 METADATA 写 OSI Approved，随包 LICENSE 给出 APSW 的许可条款及替代许可选项；SQLite3 Multiple Ciphers 上游仓库提供 MIT 许可。它们不自动构成整个二进制及所有第三方源码的完整许可证清单，发布前仍需逐项核对并保留应有声明。来源：[候选仓库 LICENSE](https://github.com/utelle/apsw-sqlite3mc/blob/sqlite3mc/LICENSE)、[SQLite3 Multiple Ciphers LICENSE](https://github.com/utelle/SQLite3MultipleCiphers/blob/main/LICENSE)。
