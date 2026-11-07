# Python 3.14 隔离兼容性实验

状态：仅供开发与合成数据测试，不替换系统 Python，也不是已发布的产品环境。2026-11-07。

## 已完成

- 从 Python 官方发布页下载 Windows x64 embeddable Python 3.14.10，核对发布页 SHA-256 后解压到项目内 .jarvis-python-3.14.10。它报告 OpenSSL 3.5.9；OpenSSL 3.5 是目前受支持的 LTS 系列，仍需持续更新安全补丁。
- 下载 Python 发布说明所列 pip 26.2.2 wheel，核对 SHA-256 后从独立目录临时加载；依赖放在项目内 .jarvis-python314-deps，不修改系统 Python 或原有 .venv。
- 已在这一运行环境中通过本机模拟邮件 HTTP、待办/检索和重启/owner 隔离测试；较早的一键回归结果为 30 PASS、2 SKIP（最新结果见项目日志）。两个跳过项是此解释器未安装 SQLCipher，不能解读成加密测试通过。
- 现有 requirements.txt 是版本范围，不是带哈希的完整锁文件；隔离环境只用于兼容性实验，正式发布前必须锁定依赖、验证发行物并建立安全更新流程。

## 当前依赖快照（无哈希，不能作为完整锁文件）

```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.16.0
click==8.5.1
fastapi==0.162.0
h11==0.16.0
httptools==0.9.0
idna==3.20
opentelemetry-api==1.45.2
pydantic==2.14.3
pydantic_core==2.50.4
python-dotenv==1.2.4
PyYAML==6.0.3
starlette==1.10.0
typing-inspection==0.4.5
typing_extensions==4.16.0
uvicorn==0.54.0
watchfiles==1.3.0
websockets==17.4
```

## 尚未完成

- 仍需能在 Windows 上维护、审查并使用受支持密码库的 SQLCipher 发行物。已核验的三个驱动发行物均报告 OpenSSL 3.6.0，已停止公开安全维护。
- embeddable Python 没有内置 venv/ensurepip；当前通过专用目录和明确的模块路径加载依赖。自动升级、签名校验、发布包、备份和恢复均未实现。
- 主应用 SQLite 依旧明文；不得导入真实个人内容，也没有 OAuth、外部发送或端到端同步。

参考：[Python 3.14.10 官方发布](https://www.python.org/downloads/release/python-31410/)、[OpenSSL 支持周期](https://openssl-library.org/roadmap/)、[pip 官方发布与校验示例](https://www.python.org/downloads/release/python-3149/)。

2026-11-07：另在独立目录测试 APSW + SQLite3 Multiple Ciphers 候选，详情见 DATA_SECURITY_DESIGN.md；它不是 SQLCipher 原构建，尚未接应用或完成安全审查。
