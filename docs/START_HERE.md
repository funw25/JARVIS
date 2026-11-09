# 从这里开始：Jarvis 本地演示与验证

目前交付的是 Jarvis 后端的虚构数据原型，HUD 尚未连接后端，也没有真实邮箱或 AI 模型接入。主应用数据库尚未加密：请勿输入真实邮件、聊天记录、密码、密钥或其他私人资料。

## 运行虚构数据演示

打开 Windows PowerShell，复制以下整段。它使用项目内隔离的 Python 和项目内临时目录，不安装软件，不连接账户。

```powershell
$jarvisRoot = Join-Path $env:USERPROFILE '.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db'
$jarvisPython = Join-Path $jarvisRoot '.jarvis-python-3.14.10\python.exe'
$jarvisTemp = Join-Path $jarvisRoot '.jarvis-tmp'
New-Item -ItemType Directory -Path $jarvisTemp -Force | Out-Null
$env:TEMP = $jarvisTemp
$env:TMP = $jarvisTemp
& $jarvisPython (Join-Path $jarvisRoot 'backend\demo_jarvis.py')
```

预期显示 1 封需要回复的虚构邮件、2 条待办、1 条笔记，以及本地假模型和模拟审批状态。演示使用独立临时数据库，退出时清理；没有邮件发送器。TEMP/TMP 的调整仅影响当前 PowerShell 窗口。

若提示找不到项目路径或 Python，可能是项目被移动或隔离运行环境被清理；先保留完整错误信息，不要改用真实数据重试。系统 Python 3.13 也曾运行过此演示，但其旧加密网络库不适合正式联网。

## 验证 M1 虚构数据闭环

运行前面设置路径的命令后执行：

```powershell
& $jarvisPython (Join-Path $jarvisRoot 'backend\verification\verify_m1.py')
```

该入口使用已锁定的 Python 3.14.10 与离线安装的依赖，检查环境锁、数据集、纵向链路、规则评测和模型适配器探针；任何一项失败或版本不符都会返回非零。成功时仍会明确输出 `M1_MODEL_QUALITY=UNVERIFIED` 与 `M1_EXIT=OPEN`：真实模型质量尚未测量，不能据此接入私人资料。

## 自己验证本地接口

运行上面设置路径的代码后，可以继续执行：

```powershell
& $jarvisPython (Join-Path $jarvisRoot 'backend\verification\verify_http.py')
```

它自动启动并关闭仅监听本机的临时测试服务，检查虚构待办、邮件、知识和提醒等工作流，不读取现有资料、不接外部账户。看到 HTTP_VERIFICATION=PASS 表示该次接口检查通过，不表示产品加密、真实邮箱或云模型已可用。

如需运行全部检查：

```powershell
& $jarvisPython (Join-Path $jarvisRoot 'backend\verification\verify_all.py')
```

如需**把整套检查跑在实验加密后端上**（随机密钥、临时虚构库，默认存储不变）：

```powershell
& $jarvisPython (Join-Path $jarvisRoot 'backend\verification\verify_adapter_suite.py')
```

如需验证**备份上传与远端恢复**（需要 Cloudflare R2 凭据，独立于离线套件）：

```powershell
# 凭据只放在仓库外：%USERPROFILE%\.jarvis\r2.env
& $jarvisPython (Join-Path $jarvisRoot 'backend\verification\verify_vault_backup_upload.py')
```

如需**配置自动备份**（口令存 Windows 凭据管理器，不落盘）：

```powershell
$cli = Join-Path $jarvisRoot 'backend\experimental\backup_cli.py'
& $jarvisPython $cli store-passphrase        # 交互式录入一次
& $jarvisPython $cli show-recovery-code      # 抄写离线保存
& $jarvisPython $cli run --vault <vault 目录> --local <本地备份目录> [--remote]
```

不在仓库目录时，用包装脚本更省事（路径自动解析）：

```powershell
& 'C:\Users\Admin\.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db\tools\backup\backup.cmd' store-passphrase
```

输出分别列出 PASS、FAIL、SKIP；有跳过项时脚本按设计返回非零，跳过并非通过。**2026-11-09 实测：62 个脚本项通过、0 项失败、0 项跳过**（`ALL_SELECTED_CHECKS=PASS`）；3.13 对照为 46 通过 / 16 跳过。62 项包含 `driver_provenance`，该项被计作 PASS 时仍可打印 `G1_GATE=OPEN`，所以总数不表示供应链门槛通过。`sqlcipher3-wheels` 位于 `.jarvis-python314-deps`，`apsw-sqlite3mc` 位于 `.jarvis-python314-apswmc-probe`；这两个目录是本机实验资产，缺失时对应检查会 SKIP。加密候选仍未获真实数据放行。

## 工作节奏与推送约定

- 每完成一个**大阶段**（一个可独立验收的交付：S1–S5 的一段、某个 G 门槛、评审包等）做一次仓库推送。
- 推送频率上限：**每天最多一次**。同一天内的多个小改动先在本地提交，合并进当天那一次推送。
- 每次推送前：跑对应验证 + 完整回归（当前 62 项），并更新 `docs/PROJECT_LOG.md`、`docs/CURRENT_STATUS.md` 与相关设计文档里的数字；文档数字与代码不一致视为未完成。
- 推送命令必须带代理：`git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push origin main`。
- 存在未推送提交时，汇报里必须写明，避免远端看起来"落后"却无人知道。

项目进度和下一步见 [项目日志](PROJECT_LOG.md)、[路线图](ROADMAP.md) 和 [本地数据保护方案](DATA_SECURITY_DESIGN.md)。
