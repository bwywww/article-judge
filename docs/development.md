# 开发指南

## 安装依赖

使用 Python 3.10 或更高版本。创建并激活虚拟环境后，在仓库根目录安装开发依赖。

```bash
python -m pip install -r requirements-dev.txt
```

Node.js 22 用于浏览器 JavaScript 语法检查。前端使用原生 ES Module，不需要 npm 安装或打包。

## 运行检查

测试脚本为独立入口，不使用 pytest 自动发现。脚本使用临时数据目录和假 AI。不要为测试设置指向学校数据的 `ESSAY_DATA_DIR`。

| 命令 | 检查内容 |
| --- | --- |
| `python tools/architecture_test.py` | 模块边界和依赖约束。 |
| `python tools/roster_privacy_test.py` | 名单导入不调用外部 AI 或网络。 |
| `python tools/ai_endpoint_security_test.py` | AI 地址白名单、持久配置校验和禁止重定向。 |
| `python tools/regression_test.py` | 账号、权限和作文批改主流程。 |
| `python tools/archive_features_test.py` | 历史学年、稿件版本和归档功能。 |
| `python tools/ocr_jobs_test.py` | OCR 持久任务和导入流程。 |
| `python tools/data_recovery_test.py` | 数据目录定位、旧库迁移和恢复。 |
| `python tools/reporting_test.py` | 报告生成和访问权限。 |
| `python tools/wsgi_smoke_test.py` | Waitress 生产入口的 HTTP 冒烟测试。 |
| `python tools/load_test.py` | 隔离环境下的并发与容量检查。 |

检查 Python 语法。

```bash
python -m compileall -q app.py database.py reporting.py wsgi.py essay_platform tools
```

在 PowerShell 中检查 JavaScript 语法。

```powershell
$failed = $false
Get-ChildItem static/js -Recurse -Filter *.js | ForEach-Object {
  Get-Content -Raw -Encoding UTF8 $_.FullName | node --input-type=module --check
  if ($LASTEXITCODE -ne 0) { $failed = $true }
}
if ($failed) { exit 1 }
```

GitHub Actions 的执行方式以 [CI 配置](../.github/workflows/ci.yml) 为准。容量测试单独运行，不代表实际学校部署的性能保证。

## 模块约束

| 目录或文件 | 职责 |
| --- | --- |
| `app.py` | 本地启动入口和测试兼容导出。 |
| `wsgi.py` | 生产 WSGI 入口。 |
| `essay_platform/application.py` | 创建 Flask 应用并注册模块。 |
| `essay_platform/bootstrap.py` | 启动、恢复和关闭。 |
| `essay_platform/core/` | 配置、权限、错误、审计和进程状态。 |
| `essay_platform/modules/` | HTTP 参数、权限和响应。 |
| `essay_platform/services/` | 业务规则、事务和状态机。 |
| `essay_platform/repositories/` | 领域 SQL。 |
| `essay_platform/persistence/` | SQLite 连接、结构和迁移。 |
| `essay_platform/adapters/` | AI、文档和报告外部适配。 |
| `essay_platform/workers/` | 批改队列和 OCR 队列。 |
| `static/`、`templates/` | 浏览器脚本、样式和页面模板。 |
| `tools/` | 测试和开发工具。 |

HTTP 模块不直接执行 SQL、AI 请求或文件解析。业务服务不依赖 Flask。仓库层不处理 HTTP 或外部 AI 调用。保留单校、单服务器、SQLite 和单进程队列的运行边界。

## 测试资料

`testdata/` 保存本地学校资料，默认忽略。自动检查不依赖这些文件。共享测试数据时使用人工构造的数据，并与学生名单、真实作文和生产数据库分开。

`tools/deepseek_delegate.py` 是可选开发工具。候选输出保存在 `.ai-work/`。该工具的密钥与网页业务密钥分开，运行常规测试无需配置。
