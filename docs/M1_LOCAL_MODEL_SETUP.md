# M1 本地模型准备记录

**日期：** 2026-11-09
**状态：** Ollama CLI 已在项目隔离目录取得并校验；本地服务与模型权重尚未就绪，M1 模型质量仍未验证。

## 选择与边界

- 机器：15.7 GB RAM（检查时空闲约 4.8 GB）、i5-1145G7、Intel Iris Xe 共享显存；首轮按 CPU 推理估算。
- 首个候选：`qwen3:4b-instruct-2507-q4_K_M`。Ollama [官方标签列表](https://ollama.com/library/qwen3/tags)标注约 2.5 GB、Q4_K_M。这里只是适配硬件的起点，质量要以 53 条虚构样本的实测为准。
- Ollama CLI：官方 GitHub 发布 `v0.40.15` 的 `ollama-windows-amd64.zip`，1,614,941,966 字节，SHA-256 `9ca74a7ab66ec86870b1eecac7df160df08dc4e3a291ab5d2d59bcf9310221c6`。项目内 `tools/models/bootstrap_ollama.py` 下载、核对哈希、解压到忽略目录 `.jarvis-ollama/runtime-v0.40.15`；`ollama.exe --version` 报客户端 0.40.15。[官方发布文件及摘要](https://github.com/ollama/ollama/releases/expanded_assets/v0.40.15)
- 没有登录 Ollama，没有拉取模型，也没有把 53 条样本交给模型。归档和权重均不入 Git。

## 运行环境阻塞

在本会话工具的运行环境里，`ollama serve` 前台和隐藏后台两种方式均失败：它会在 `%USERPROFILE%\.ollama\id_ed25519` 创建本机密钥，但进程收到 `Access is denied`。通过工具创建 `.ollama` 目录后仍无法打开密钥文件，因此端口 11434 没有服务。该密钥是 Ollama 本机配置材料，不能当作已登录账号或已下载权重的证据。

用户第一次运行准备脚本时只看到了进程启动行；检查发现进程随即退出，日志仍报同一权限错误。核对后，工具创建的 `.ollama` 是空目录，受限进程对它只有读权限。已验证绝对路径及空目录状态并移除此目录，让用户的普通 PowerShell 在重试时由 Ollama 自行创建。脚本现在会短暂检查进程是否立即退出，失败时直接给出最近错误，不再把创建进程表述为服务就绪。

这说明当前自动化运行器不能完成 Ollama 的正常首次启动；不推断普通 Windows PowerShell 也会失败。没有使用云端模型或其他供应商绕过此门槛。

## 下一次动作：在普通 Windows PowerShell 运行一次

项目已备好 `tools/models/setup_local_model.ps1`。它调用固定版本的 CLI，以 `127.0.0.1:11434` 启动隐藏本机服务，设置 `OLLAMA_NO_CLOUD=1`，检查日志确实报告云功能关闭，然后拉取指定本地模型；最后从 `/api/tags` 核对模型名、非零大小和摘要。**它会写入普通 Ollama 所需的 `%USERPROFILE%\.ollama` 本机配置目录，不登录账号。**

```powershell
& C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db\tools\models\setup_local_model.ps1"
```

成功时应看到 `OLLAMA_LOCAL_API=PASS` 和 `LOCAL_MODEL_READY=PASS model=qwen3:4b-instruct-2507-q4_K_M digest=...`。如果失败，保留报错和 `.jarvis-ollama/server.stderr.log` 的末尾几行即可；**不要发送 `id_ed25519` 或任何密钥**。

随后先用 3 条虚构样本试跑，再评测全部 53 条并保存规则与模型逐项对比。完整模型运行前，M1 仍是 `OPEN`。Ollama 官方 [FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)明确给出 `OLLAMA_NO_CLOUD=1`；本项目还拒绝 `:cloud` / `-cloud` 标签及 Ollama 非回环地址，并在开始评测前要求本地模型具有大小与摘要。
