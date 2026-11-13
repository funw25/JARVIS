# M1 本地模型准备记录

**日期：** 2026-11-09
**状态：** Ollama CLI 和本地模型已取得并验证；v1 失败基线、v2 两次完整原集与留出集评测已完成。M1 虚构数据研究闭环收口，真实资料未放行。

## 选择与边界

- 机器：15.7 GB RAM（检查时空闲约 4.8 GB）、i5-1145G7、Intel Iris Xe 共享显存；首轮按 CPU 推理估算。
- 首个候选：`qwen3:4b-instruct-2507-q4_K_M`。Ollama [官方标签列表](https://ollama.com/library/qwen3/tags)标注约 2.5 GB、Q4_K_M。这里只是适配硬件的起点，质量要以 53 条虚构样本的实测为准。
- Ollama CLI：官方 GitHub 发布 `v0.40.15` 的 `ollama-windows-amd64.zip`，1,614,941,966 字节，SHA-256 `9ca74a7ab66ec86870b1eecac7df160df08dc4e3a291ab5d2d59bcf9310221c6`。项目内 `tools/models/bootstrap_ollama.py` 下载、核对哈希、解压到忽略目录 `.jarvis-ollama/runtime-v0.40.15`；`ollama.exe --version` 报客户端 0.40.15。[官方发布文件及摘要](https://github.com/ollama/ollama/releases/expanded_assets/v0.40.15)
- 没有登录 Ollama；53 条虚构样本只送往本机回环地址的模型服务。归档和权重均不入 Git。

## 运行环境阻塞

在本会话工具的运行环境里，`ollama serve` 前台和隐藏后台两种方式均失败：它会在 `%USERPROFILE%\.ollama\id_ed25519` 创建本机密钥，但进程收到 `Access is denied`。通过工具创建 `.ollama` 目录后仍无法打开密钥文件，因此端口 11434 没有服务。该密钥是 Ollama 本机配置材料，不能当作已登录账号或已下载权重的证据。

用户第一次运行准备脚本时只看到了进程启动行；检查发现进程随即退出，日志仍报同一权限错误。核对后，工具创建的 `.ollama` 是空目录，受限进程对它只有读权限。已验证绝对路径及空目录状态并移除此目录，让用户的普通 PowerShell 在重试时由 Ollama 自行创建。脚本现在会短暂检查进程是否立即退出，失败时直接给出最近错误，不再把创建进程表述为服务就绪。

用户第二次重试仍报无法创建 `%USERPROFILE%\.ollama`，说明该会话的普通 PowerShell 同样不能在用户主目录建 Ollama 配置。继续重复原启动命令不会解决此权限限制。

## 项目内独立配置目录：已验证的解决办法

`tools/models/ollama_in_project.py` 只在 Ollama **子进程**环境里把 Windows 用户配置路径指向项目的忽略目录 `.jarvis-ollama/isolated-profile`，同时设置 `OLLAMA_MODELS`、`OLLAMA_NO_CLOUD=1` 和 `OLLAMA_HOST=127.0.0.1:11434`；调用方 PowerShell、系统环境和其他进程的用户路径不变。首次本机密钥已在该隔离目录创建，不是 Ollama 登录凭据；**不要提交或分享 `id_ed25519`**。

在此配置下，`ollama serve` 已持续运行，`GET /api/version` 返回 `0.40.15`，`GET /api/tags` 初始返回空列表。GPU 发现进程在当前环境异常退出，Ollama 回退到 CPU，报告系统内存 15.7 GiB、当时可用约 5.0 GiB。模型已下载到 `.jarvis-ollama/models`；`/api/tags` 返回标签 `qwen3:4b-instruct-2507-q4_K_M`、大小 2,497,293,803 字节、摘要 `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`，与官方标签摘要一致。

用固定提示词、`temperature=0`、`seed=42`、`num_ctx=4096` 完成五类各一条试跑：分类 2/5 正确，注入样本判成 `noise`（注入召回 0/1），证据逐字率 5/5，总耗时约 35 秒。该结果保存在忽略目录 `.jarvis-ollama/results/pilot-five-classes.json`；是部分样本。

同一配置随后完成 53 条全量评测，报告在忽略目录 `.jarvis-ollama/results/full.json`。`report.items=53`、`dataset_total=53`、`complete_dataset=true`、逐项对比 53 条；模型摘要与 `/api/tags` 一致。分类准确率 43.4%，需回复召回 0/15，提示注入召回 1/8，耗时 311.4 秒。模型只在 CPU 上运行；这份报告是**失败基线**，不得据此接入真实邮件或自动建立任务/草稿。完整指标和错误见 [M1 结果](M1_RESULTS.md)。

`start_ollama.ps1`、`serve_ollama.ps1`、`setup_local_model.ps1` 已改为使用项目内启动器。服务已经运行时不要再次执行安装脚本；需要在以后重启本地服务时才使用下面的命令：

```powershell
& C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db\tools\models\setup_local_model.ps1"
```

脚本预期检查 `OLLAMA_LOCAL_API=PASS` 和 `LOCAL_MODEL_READY=PASS model=qwen3:4b-instruct-2507-q4_K_M digest=...`。如果失败，保留错误和 `.jarvis-ollama/server.stderr.log` 的末尾几行即可。

以后重新评测可运行 `& .\.jarvis-python-3.14.10\python.exe tools\models\run_m1_local.py`；`--pilot` 只跑五类各一条。每次评测前仍应核对服务器的本地配置、模型摘要及报告是否覆盖全部样本。Ollama 官方 [FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)明确给出 `OLLAMA_NO_CLOUD=1`；本项目还拒绝 `:cloud` / `-cloud` 标签及 Ollama 非回环地址，并在开始评测前要求本地模型具有大小与摘要。

## 2026-11-14 复核：启动在另一种方式下可用

本会话复核时，模型权重其实**已经存在**：`.jarvis-ollama\models` 下有 `qwen3:4b-instruct-2507-q4_K_M`（2.5 GB，摘要 `0edcdef3…`，4 天前写入），`/api/tags` 可读出非零大小与摘要。上一节记录的失败只发生在**无法写用户主目录的受限运行器**里，不是模型或安装损坏。

用另一条同样只影响子进程的路径启动成功，未使用 `ollama_in_project.py`：

```powershell
$base = "<repo>\.jarvis-ollama"
$env:OLLAMA_MODELS = "$base\models"
$env:OLLAMA_HOST   = "127.0.0.1:11434"
$env:OLLAMA_NO_CLOUD = "true"
$env:USERPROFILE = "$base\home"          # 让 ~/.ollama 落在项目内
$env:HOMEDRIVE = "C:"; $env:HOMEPATH = ($env:USERPROFILE -replace '^.:','')
& "$base\runtime-v0.40.15\ollama.exe" serve
```

日志确认 `Ollama cloud disabled: true`，端口 11434 可用，`/api/version` 返回 `0.40.15`；GPU 发现失败（`llama-server --list-devices` → `0xc0000005`），推理全在 CPU。随后完成两次 53 条全量评测，指标见 [M1 结果 §4 的 2026-11-14 小节](M1_RESULTS.md)。这不能证明受限运行器也能启动；它只证明"把配置目录重定向到可写位置"这一条路径在本机可用。

## 后续 v2 评测入口

项目内 `ollama_in_project.py` 将配置仅设在子进程，避免在当前 PowerShell 窗口修改用户目录变量。服务启动后，可用以下命令只评测固定的虚构样本；`--holdout` 切换到另一个 20 条虚构集，`--out` 保留各次报告，`--prompt-version v2` 在模型指令与输入之间使用独立字段：

```powershell
& .\.jarvis-python-3.14.10\python.exe tools\models\run_m1_local.py --prompt-version v2 --out .jarvis-ollama\results\v2-full.json
& .\.jarvis-python-3.14.10\python.exe tools\models\run_m1_local.py --prompt-version v2 --holdout --out .jarvis-ollama\results\v2-holdout.json
```

本地模型仍未接入主工作流。原始报告留在忽略目录；`tools/models/export_m1_evidence.py` 可核对完整性并导出不含样本文本的精简证据。当前 v2 指标与限制见 [M1 结果](M1_RESULTS.md)。
