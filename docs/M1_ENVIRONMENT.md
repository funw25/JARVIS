# M1 离线环境：锁定与干净机器重建

**状态：** M1 离线环境已锁定并可在干净目录重建（2026-11-09）  
**范围：** 只覆盖 M1 的虚构数据链路与回归；M2 的加密驱动供应链、正式运行环境不在此页  
**产物：** `tools/env/requirements-m1.lock`、`tools/env/build-wheelhouse.ps1`、`tools/env/bootstrap.ps1`、`tools/env/test-cleanroom.ps1`、`backend/verification/verify_env_lock.py`

---

## 1. 锁定内容

| 层 | 锁定方式 |
|---|---|
| 解释器 | CPython **3.14.10**（官方 embeddable amd64 zip），SHA-256 `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159` |
| 解释器完整性 | 本项目 `.jarvis-python-3.14.10` 与官方 zip 逐文件比对：**36/37 字节一致**，唯一差异是 `python314._pth`（为挂载依赖目录而编辑） |
| 依赖 | 32 个包，版本 + wheel SHA-256 全部写入 `tools/env/requirements-m1.lock` |
| 安装方式 | 离线、`--no-index --require-hashes`，只从本地 wheelhouse 取包 |
| 校验 | `verify_env_lock.py`：解释器版本、`._pth` 挂载、32 个包版本、wheel 哈希、运行时导入 |

锁定事实由脚本核对，不靠人工声明：包版本漂移、wheel 被替换、`._pth` 丢条目都会失败。

## 2. 三条命令

```powershell
$repo = 'C:\Users\Admin\.codex\.chatgpt-projects\g-p-6ae96df8f3c48191a4e131d6589494db'

# 1) 维护者：从 lock 重建 wheelhouse（需要网络一次）
pwsh -File "$repo\tools\env\build-wheelhouse.ps1"

# 2) 新机器：从官方 zip + wheelhouse 离线重建解释器与依赖
pwsh -File "$repo\tools\env\bootstrap.ps1" -ZipPath <python-3.14.10-embed-amd64.zip>

# 3) 证明干净目录可重建（不碰仓库自带的依赖目录）
pwsh -File "$repo\tools\env\test-cleanroom.ps1" -ZipPath <python-3.14.10-embed-amd64.zip>
```

`bootstrap.ps1` 会：核对 zip 哈希 → 解压 → 重写 `python314._pth`（加入依赖目录）→ 用 `--require-hashes` 离线装 32 个 wheel → 跑 `verify_env_lock.py`。

## 3. 干净机器重建的实测证据

2026-11-09 在项目内临时目录跑 `test-cleanroom.ps1`（全新解压的解释器 + 全新依赖目录，不使用 `.jarvis-python314-deps`）：

```
INTERPRETER_PINNED=PASS python=3.14.10 impl=cpython
PTH_EXPOSES_DEPS=PASS deps=...\jarvis-cleanroom-9e989e54\deps entries=['python314.zip', '.', '..\deps']
PACKAGES_MATCH_LOCK=PASS count=32
WHEELHOUSE_MATCHES_LOCK=PASS wheels=32
RUNTIME_IMPORTS=PASS fastapi=0.162.0 pydantic=2.14.3
M1_ENV_LOCK=PASS
PASS verify_env_lock.py
PASS verify_m1_dataset.py
PASS verify_m1_pipeline.py
PASS verify_m1_evaluation.py
M1_OFFLINE_CHECKS=PASS count=4 skip=0
M1_MODEL_QUALITY=UNVERIFIED
M1_EXIT=OPEN
CLEAN_ROOM_REBUILD=PASS
```

注意最后两行：环境可重建**不改变** M1 的门槛状态——模型质量仍未测量。

## 4. 仓库内的位置

| 路径 | 说明 |
|---|---|
| `.jarvis-python-3.14.10/` | 便携解释器（gitignore，由 bootstrap 重建） |
| `.jarvis-python314-deps/` | 依赖目录（gitignore，由 bootstrap 重建） |
| `.jarvis-wheelhouse/` | 32 个 wheel（gitignore，由 build-wheelhouse 重建） |
| `tools/env/requirements-m1.lock` | 版本 + 哈希锁（入库） |
| `tools/env/*.ps1` | 构建、引导、干净房间测试脚本（入库） |
| `backend/verification/verify_env_lock.py` | 环境校验，已注册进回归与 `verify_m1.py` |

## 5. 已知边界

- 便携解释器**不带 pip**：解包 wheel 需要一个外部带 pip 的解释器（脚本默认 `.venv`），这是离线约束不是缺陷。
- 官方 zip 必须**先联网取一次**（或由他人拷贝），之后全程离线；哈希已固定在 `bootstrap.ps1` 与本文。
- 只有 Windows x64 路径被验证；macOS/Linux 未验证。
- 这是**进程级**环境隔离，不是沙箱或虚拟机；不防御同用户恶意程序。
- 解释器与依赖目录都在仓库内，`git clean -x` 会删除它们，必须用 bootstrap 重建。
- 首次取 zip 的双重校验依赖 python.org 的 HTTPS 与记录哈希；未做 GPG 签名校验。
