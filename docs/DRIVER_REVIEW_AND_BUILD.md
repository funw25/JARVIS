# 加密驱动审查清单与自建草案（G1）

**状态：** G1 **未通过**（清单与检测已就绪；自建构建未执行）  
**更新：** 2026-11-09
**结论先行：** 当前实验驱动可用于合成数据验证，但**不得承载真实个人数据**。两条缺口：许可证文本不完整、加密原语来源无法从产物证实。  
**配套：** [评审包](REVIEW_PACKET.md)、[切换计划](STORAGE_SWITCH_PLAN.md)、[驱动清单 `driver-manifest.json`](driver-manifest.json)

---

## 1. 现状盘点（实测，非引用）

| 项 | 值 |
|---|---|
| wheel | `apsw_sqlite3mc` 3.54.0.0（PyPI，作者 Ulrich Telle，仓库 `utelle/apsw-sqlite3mc`） |
| APSW | 3.54.0.0 |
| SQLite | 3.54.0 |
| SQLite3 Multiple Ciphers | 2.6.1 |
| 主二进制 | `apsw/__init__.cp314-win_amd64.pyd`，2,939,392 字节，SHA-256 `a9267aeb…bd9d53` |
| 附带二进制 | `apsw/_unicode.cp314-win_amd64.pyd`（732,160 字节）+ `apsw/sqlite_extra_binaries/*.dll`（扩展模块，非加密） |
| 许可证文本 | `dist-info/licenses/LICENSE`，**仅 APSW**（zlib 风格，Roger Binns）；**缺** SQLite3MC 的 MIT 与 SQLite 的 public-domain 声明 |
| 加密后端 | 二进制中**找不到** `OpenSSL`/`Botan`/`libssl` 版本串；仅有 `sqlcipher` 字样 → **无法从产物确认原语来源与版本** |
| 运行时暴露 | 仅 `apsw.mc_version`；无 OpenSSL 版本 API |

> 曾把已测 SQLCipher wheel 的 OpenSSL 3.6 维护风险错误归到当前 APSW/SQLite3MC wheel。当前 SQLite3MC 源码检查未找到 OpenSSL/Botan 引用，二进制字符串也不能证明实际构建配置；应以可复核的源码、构建记录和二进制依赖清单确定原语及依赖。G1 仍未通过。

**自动检测：** `backend/verification/verify_driver_provenance.py` 会比对 `docs/driver-manifest.json` 中记录的哈希与版本，并打印许可证覆盖与后端可识别性。当前输出：

```
DRIVER_VERSIONS_MATCH_MANIFEST=PASS
DRIVER_FILE_HASHES_MATCH_MANIFEST=PASS files=3
DRIVER_LICENSE_TEXT=PRESENT APSW
DRIVER_CRYPTO_BACKEND=UNKNOWN
G1_GATE=OPEN (license texts incomplete: APSW; crypto backend not identifiable from the binary)
```

## 2. G1 检查清单

每项都写"怎么验证"与"通过标准"；状态以当前产物为准。

| # | 检查项 | 验证方法 | 通过标准 | 当前 |
|---|---|---|---|---|
| 1 | 发布来源一致 | PyPI 元数据作者/仓库、上游 release 页 | 三者指向同一项目与同一发布者 | ✅ |
| 2 | 产物完整性 | 下载 wheel 计算 SHA-256，与锁文件/清单一致 | 哈希固定并可复核 | ✅ |
| 3 | 许可证文本完整 | `dist-info/licenses/` + 安装包 licences 目录 | APSW、SQLite3MC(MIT)、SQLite(public domain)，以及实际引入组件的文本齐全 | ❌ 仅 APSW |
| 4 | SBOM | 生成组件表：名称/版本/哈希/许可证/来源 URL | 每个随附二进制可追溯到组件 | ❌ 未生成 |
| 5 | 加密原语来源可验证 | 固定源码提交、构建参数和编译/链接记录，核对二进制依赖；运行时标识仅作旁证 | 能追踪实际算法实现和依赖到源码与构建产物 | ❌ 当前 wheel 的构建链不可复核 |
| 6 | 实际依赖维护状态 | 先以构建记录和二进制依赖清单确定组件，再对照各上游支持周期 | 无已知停止维护的实际依赖 | ❌ 当前 wheel 的实际依赖未证实；SQLCipher 候选的 OpenSSL 3.6 结论不能外推 |
| 7 | 可复现构建 | 同源同参构建两次比对哈希 | 两次一致（或差异可解释） | ❌ 未自建 |
| 8 | 漏洞响应渠道 | 上游 issue/security 政策、CVE 跟踪方式 | 有明确订阅与响应路径 | ⚠️ 仅上游仓库 |
| 9 | 分发义务 | 判定"自用"还是"对外分发" | 分发时随包提供全部许可证文本 | ⚠️ 未分发 |
| 10 | 树外依赖 | 检查是否加载系统 DLL/OpenSSL | 依赖封闭在 wheel 内或显式声明 | ⚠️ 未核 |
| 11 | 命名与冒充 | 包名、作者、仓库、签名是否一致 | 无同名混淆风险 | ✅ |
| 12 | 测试覆盖 | 在候选驱动上跑所选版本的全套回归 | 当次清单零 FAIL、零 SKIP；另列独立发布门槛 | ✅ 先前 57 项脚本回归通过；其中 `driver_provenance` 仍报告 `G1_GATE=OPEN`，不算 G1 放行 |

**G1 通过标准（DoD）：** 1–7 全部为 ✅，且 8/9/10 有书面结论。仅 1/2/11/12 通过不足以放行真实数据。

## 3. 自建草案（三方案）

| 方案 | 组成 | 优点 | 代价 | 建议 |
|---|---|---|---|---|
| **A（推荐验证路线）** | 固定并审查 SQLite3MC + APSW 源码与 ChaCha20 构建配置；只有实际需要外部密码库时才引入并审查它 | 与现有适配器 API 一致；可记录实际原语与依赖 | 需构建工具链、组件来源审查与自行打包 wheel | 候选，仍受 G1/G2 门槛约束 |
| B | [SQLCipher v4.20.0](https://github.com/sqlcipher/sqlcipher/releases/tag/v4.20.0) 源码 + 待审查的 Python 绑定；[sqlcipher3 0.6.5](https://pypi.org/project/sqlcipher3/0.6.5/) 已列 Windows cp314 wheel | 可评估 sqlite3 风格 API 与上游新版修复 | cp314 wheel 的存在不证明编译用的 SQLCipher/密码库版本或来源；`PRAGMA rekey`、许可、维护和恢复语义均需实测 | 备选，不因已有 wheel 直接放行 |
| C | SQLite3MC + **Botan**（无 OpenSSL） | 彻底绕开 OpenSSL 供应链 | Botan 引入新的构建与许可证组合，仍需审查 | 若 OpenSSL 供应链无法满足时 |

### 方案 A 的构建步骤（草案，未执行）

1. **工具链**：MSYS2（`mingw-w64-x86_64-gcc`、`cmake`、`ninja`、`perl`），或 VS2022 + vcpkg。记录工具链版本。
2. **钉住版本**（建议写进 `tools/driver/versions.env`）：
   - 实际依赖的密码库及版本（若此配置不需要外部密码库，不添加 OpenSSL）
   - SQLite `3.54.0` amalgamation
   - SQLite3 Multiple Ciphers `2.6.x`（与当前运行时一致的次版本）
   - APSW `3.54.0.0`
3. **校验来源**：每个 tarball 记录 SHA-256 与官方发布页 URL；写入 SBOM。
4. **构建**：先固定 SQLite3MC 的算法、源码提交和编译参数，核对实际链接依赖；不要仅因旧文档的 D6 结论加入 OpenSSL。记录工具链和构建日志。
5. **打包**：生成 wheel，内含 `licenses/`（APSW、SQLite3MC、SQLite，以及实际引入组件的相应声明）与 `sbom.json`。
6. **记录**：更新 [`docs/driver-manifest.json`](driver-manifest.json)（版本、文件哈希、许可证覆盖、后端标识），提交。
7. **验证**：
   ```powershell
   & $py backend\verification\verify_driver_provenance.py   # 期望 G1_GATE=CLOSED
   & $py backend\verification\verify_all.py                 # 期望当次清单全部通过，无 SKIP
   & $py backend\verification\verify_adapter_suite.py       # 同上，跑在加密后端
   ```
8. **复核**：重复步骤 4–6 两次，确认产物哈希一致（可复现），否则记录差异原因。

### 构建后必须产出

| 产出 | 位置 | 说明 |
|---|---|---|
| 组件清单 | `docs/sbom.json` | 名称/版本/哈希/许可证/来源 |
| 许可证集合 | 安装包 `licenses/` | APSW、SQLite3MC、SQLite 与实际引入组件的声明齐全 |
| 驱动清单 | `docs/driver-manifest.json` | 更新为自建产物 |
| 构建脚本 | `tools/driver/build.ps1` | 可重跑，含版本钉扎与校验 |
| 回归结果 | 本文末尾追加 | 当次清单数量、环境指纹 + 适配器整套 |

## 4. 风险与未决

| 风险 | 说明 | 处置 |
|---|---|---|
| 自建 wheel 的 ABI 兼容 | CPython 3.14 cp314 需与运行环境一致 | 构建时指定 `--python-tag cp314`，并保留 3.13 对照 |
| 实际密码组件的维护窗口 | 以自建产物的可复核依赖清单为准，不能从其他候选推断 | 写入定期检查项 |
| 构建可复现性 | MinGW 产物常含时间戳/路径 | 用 `-ffile-prefix-map`、固定时间戳，或接受"可解释差异" |
| 许可证合规 | SQLite3MC MIT + SQLite public domain | 打包时随附，分发前复核 |
| 与上游漂移 | 自建后无法直接跟随 PyPI | 每季度评估上游安全公告并重建 |

## 5. 参考

- APSW：<https://github.com/rogerbinns/apsw>
- SQLite3 Multiple Ciphers：<https://github.com/utelle/SQLite3MultipleCiphers>
- 组合包：<https://github.com/utelle/apsw-sqlite3mc>
- OpenSSL 发布与支持周期：<https://openssl-library.org/source/>
- SQLCipher（备选）：<https://github.com/sqlcipher/sqlcipher>
