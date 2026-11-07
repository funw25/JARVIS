# Windows 环境问题：`pwsh` 全部命令以 0xC0000142 退出

**状态：** 已实测定位（2026-11-06）  
**一句话：** 不是 PowerShell 的问题，也不是缺 PowerShell 7；是 DSH 的 Windows 受限令牌在这台机器上完成不了子进程启动。**装 pwsh 7 修不好它。**

## 1. 实测对照

| 会话权限 | `echo hi` |
|---|---|
| `workspace-write` / `read-only`（受限） | `[exit code: 3221225794]`，stderr 为空，每次必现 |
| `danger-full-access`（完全权限） | 正常 |

`3221225794` = `0xC0000142` = `STATUS_DLL_INIT_FAILED`：进程已创建、在 DLL 初始化阶段退出，所以没有任何输出。

## 2. 关键结论：与 PowerShell 无关

我手工构造了与 DSH 同形态的令牌（`WRITE_RESTRICTED` + Low 完整性 + restricting SID 列表），并把子进程的 stdout/stderr 接到真实文件句柄（排除"没有标准输入输出"的干扰），对 **cmd.exe** 和 **powershell 5.1** 同时测试：

| 令牌形态 | cmd.exe | powershell 5.1 |
|---|---|---|
| 不限制（对照） | 退出 7，输出 `MARKER-CMD` | 退出 7，输出 `MARKER-PS` |
| **仅 Low 完整性** | 退出 7，输出正常 | 退出 7，输出正常 |
| `WRITE_RESTRICTED[Everyone]` + Low | 启动后不退出（`0x103`） | 同左 |
| `WRITE_RESTRICTED[Everyone, 登录SID]` + Low | 启动后不退出（`0x103`） | 同左 |

三点：

1. **Low 完整性本身无害**——两个程序都正常跑完；
2. **只要加上 restricting SID 列表，任何程序都完不成启动**（cmd 和 PowerShell 一样），所以这不是 PowerShell 版本问题；
3. DSH 源码注释里的 `0xC0000142`/`0xE0434352` 讲的正是这个层：restricting 列表缺保活组时"早期 DLL 初始化会以 `0xC0000142` 死亡"。

## 3. 本机还有一个 CET 陷阱

全机唯一的 `pwsh.exe` 是 Codex 私有运行时副本（`%USERPROFILE%\.cache\codex-runtimes\...\powershell`），实测**它自己就跑不起来**：

```
Fatal error. Your Windows doesn't fully support CET. Please install all available Windows updates.
exit = 0x80131506
```

原因：

| 项 | 值 |
|---|---|
| OS | Windows 10 Pro for Workstations **22H2 / 19045.2846** |
| CPU | 11th Gen Intel i5-1145G7（硬件支持 CET） |
| CET 系统策略 | `MitigationOptions` 未设置 |
| 该副本运行时 | **.NET 10**（`mscordaccore_...10.0.1426...`） |

.NET 10 硬性要求操作系统的 CET 支持，Windows 10 不提供 → 这份副本在本机不可用。**因此"把它复制到 `C:\Program Files\PowerShell\7` 让 DSH 用"这条路走不通**。

顺带修正 ADR-004：事件日志里没有 `0xc0000142` 的 Application Error 记录，CET 策略也未强制；当时"Windows 更新/CET 导致 Python 起不来"的归因应视为未证实。

## 4. 那要不要装 PowerShell 7？

**为了修 `0xC0000142`：不需要，也没用**（第 2 节已证明与 PowerShell 无关）。

**为了 shell 质量：可以，但要挑版本。**

| 选项 | 收益 | 代价 / 风险 |
|---|---|---|
| 保持完全权限，不装 | 零成本，命令立刻可用 | 会话没有文件沙箱隔离 |
| 装 **PowerShell 7.4.x（.NET 8 LTS）** 到 `C:\Program Files\PowerShell\7` | 摆脱 5.1 的编码/语法限制；DSH 第一个探测位置 | 仍需实测受限模式是否恢复（见下） |
| 装 7.5 / 7.6（.NET 10） | 同上 | **在本机跑不起来**（CET 不支持，同第 3 节） |

安装方式（本机无 winget / choco）：从 GitHub releases 下 `PowerShell-7.4.x-win-x64.msi` 安装到默认路径即可。

**注意：装完 7.4 也不一定能回受限模式。** 第 2 节的实验里 cmd 同样启动失败，说明阻塞点在令牌/环境层。装完 7.4 后建议这样验证一次：

1. 切到 `workspace-write`；
2. 让我跑 `cmd /c echo hi`——如果 cmd 通而 pwsh 不通，说明是 PowerShell 侧残留问题；两个都不通，说明沙箱本身在本机不可用；
3. 若不通，切回完全权限继续工作，并把本文件的第 2 节证据作为反馈提交给 DSH。

## 5. 建议的当前策略

1. **现在**：保持完全权限，继续 P3/P4（原型与文档都已可用，验证链路已跑通）。
2. **可选**：装 PowerShell 7.4 提升 shell 体验；不要在 Win10 上装 .NET 10 系版本。
3. **要沙箱**：需要 DSH 侧修复或换到支持 CET 的系统版本；用户侧没有可靠的绕法。
4. **Python 运行时**：`python` 命令仍解析到 WindowsApps 商店占位符，但本机实际安装了 Python 3.13.12，位于 `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`；通过应用内直接运行器可启动。当前环境的命令启动器仍失败，IDLE 窗口捕获也超时。已用直接运行器执行 `backend/verification/verify_core.py`：语法、迁移和核心服务验证通过；FastAPI 导入因依赖缺失而跳过。

## 6. 复现脚本

`tools\env-repro\` 下（原先放在 `%TEMP%\dsh-repro`，已收进项目以免随临时目录被清理）：

| 文件 | 用途 |
|---|---|
| `repro3.ps1` | 令牌矩阵（不接标准输入输出） |
| `repro4.ps1` | 对比 cmd / 5.1 / PS7 副本 |
| `repro5.ps1` | **推荐**：令牌矩阵 + 真实重定向 stdio，结果最可靠 |
| `repro6.ps1` | 覆盖 cmd / 5.1 / 7.4 三种 shell |
| `cipher_debug.py` / `cipher_debug2.py` | SQLCipher 错误密钥行为的最小复现（解释了当时探针误报） |

用法：`powershell -NoProfile -ExecutionPolicy Bypass -File tools\env-repro\repro5.ps1`。这些脚本只做本机诊断，不接触真实数据。

## 7. 本机已安装的工具（2026-11-07 实测）

| 项目 | 状态 | 位置 / 备注 |
|---|---|---|
| Python | **3.13.12**（用户级，已装） | `%LOCALAPPDATA%\Programs\Python\Python313`；pip 25.3、SQLite 3.50.4、OpenSSL 3.0.18；`py` 启动器已装；安装包签名校验通过（Python Software Foundation） |
| 项目虚拟环境 | `.venv`（已装依赖） | fastapi 0.162.0 / pydantic 2.14.3 / uvicorn 0.54.0 / starlette 1.10.0 / sqlcipher3-wheels 0.5.7 |
| PowerShell | **7.4.22**（已装，.NET 8.0.33） | `C:\Program Files\PowerShell\7\pwsh.exe`；MSI 签名有效（Microsoft）；**受限沙箱下仍 0xC0000142**，只能在全权限下使用 |
| Office | **已存在，无需安装** | Office LTSC/ProPlus 2021 x86 zh-cn，`C:\Program Files (x86)\Microsoft Office\root\Office16`，Word / PowerPoint / Outlook / Excel / Access 16.0.14326.20454，开始菜单快捷方式齐全 |

**Office 授权现状（必须知情）：** `OSPP.VBS /dstatus` 显示 `Office21ProPlus2021VL_KMS_Client_AE`，激活类型 KMS，KMS 主机为 `kms.03k.org:1688`，剩余宽限 178 天。这不是 Microsoft 签发的授权，属于第三方 KMS 激活路径。我没有改动它。若要走正版：Microsoft 365 订阅或单位发放的正版密钥，用 ODT 重装为 Click-to-Run 后登录激活。

**PATH 生效范围：** 安装器已把 Python 写入用户 PATH、PowerShell 7 写入机器 PATH；DSH 进程是安装前启动的，因此**当前会话的子进程仍看不到它们**，需要重启 DSH（或新开会话）才会自动使用 `pwsh` 7.4。在本会话内请用完整路径或自行前置 `$env:Path`。

## 8. GitHub 推送与代理（2026-11-07）

远端仓库：<https://github.com/funw25/JARVIS>。本机实测：`api.github.com:443` 直连可用，但 **`github.com:443` 直连会超时**；系统代理已开启（`127.0.0.1:7890`，Clash 风格），git 默认不走系统代理，所以必须显式指定：

```powershell
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main
```

若希望一劳永逸，可只对该仓库设置（不要写全局，避免代理关闭后所有 git 操作失败）：

```powershell
git config --local http.proxy http://127.0.0.1:7890
git config --local https.proxy http://127.0.0.1:7890
```

凭据由 Git Credential Manager 提供（`D:\Program\Git\ucrt64\bin\git-credential-manager.exe`），已缓存可用的 GitHub 凭据；若失效，运行 `git-credential-manager github login` 重新授权。**不要把 token 写进 URL 或提交到仓库。**
