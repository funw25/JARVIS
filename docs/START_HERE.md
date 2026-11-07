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

输出分别列出 PASS、FAIL、SKIP；有跳过项时脚本按设计返回非零，跳过并非通过。**2026-11-07 实测：47 项通过、0 项失败、0 项跳过**（`ALL_SELECTED_CHECKS=PASS`）。为此把 `sqlcipher3-wheels` 装进了 `.jarvis-python314-deps`，并把 `apsw-sqlite3mc` 放在 `.jarvis-python314-apswmc-probe`；这两个目录是本机实验资产，缺失时对应检查会自动 SKIP 而不是误报通过。加密候选仍是独立实验，尚未保护主应用。

项目进度和下一步见 [项目日志](PROJECT_LOG.md)、[路线图](ROADMAP.md) 和 [本地数据保护方案](DATA_SECURITY_DESIGN.md)。
