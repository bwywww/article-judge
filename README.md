# AI 作文批改平台

面向单校、单服务器部署的 Flask 作文批改平台。管理员维护账号、班级、学年、AI 配置和备份；老师布置作业、导入作文、复核并发布结果；学生提交作业、查看历史记录和进行自主练习。

项目使用 Python 3.10+、SQLite、浏览器原生 ES Module 和 Waitress。批改使用单进程内的全局四路队列，OCR 使用独立的串行持久任务队列。

## 主要功能

- 管理员管理人员、班级、学年结转和备份恢复。
- 老师创建作业、导入文档、审核 AI 批改结果、退回重交和发布优秀作文。
- 学生提交作业、查看稿件版本、查询历史学年记录和进行自主练习。
- PDF 逐页判断文字层，扫描页和图片进入 OCR 队列。任务支持取消、失败重试和重启续跑。
- 每次提交保存独立稿件版本，发布结果保存快照，学年历史记录长期保留。

## 首次在本地运行

在仓库根目录执行命令。GitHub 页面中的文件根目录应包含 `app.py`、`README.md` 和 `requirements.txt`。

创建虚拟环境。

```bash
python -m venv .venv
```

Windows PowerShell 激活虚拟环境。

```powershell
.venv\Scripts\Activate.ps1
```

macOS 或 Linux 激活虚拟环境。

```bash
source .venv/bin/activate
```

安装依赖。

```bash
python -m pip install -r requirements.txt
```

首次克隆不包含数据库。仅在隔离的本地演示环境中，显式启用演示初始化。

Windows PowerShell 设置如下。

```powershell
$env:ESSAY_BOOTSTRAP_DEMO="1"
```

macOS 或 Linux 设置如下。

```bash
export ESSAY_BOOTSTRAP_DEMO=1
```

启动应用。

```bash
python app.py
```

默认地址为 <http://127.0.0.1:5000>。端口占用时，应用尝试后续端口，以启动日志中的地址为准。Windows 也可使用 `启动.bat`，首次初始化仍需显式设置上述环境变量。

演示初始化创建 `admin`、`mr`、`stu` 账号和测试班级 `class`。临时密码为 `ChangeMe123!`，首次登录必须修改。已有数据库无需启用演示初始化。普通启动在数据库缺失时拒绝创建空库。演示账号只能用于隔离的本地环境，不要用于生产。

## 配置 AI

业务使用两个分开的 profile。

| Profile | 环境变量 | 用途 |
| --- | --- | --- |
| `article_judge` | `ARTICLE_JUDGE_API_KEY` | 批改、提升版、姓名识别和班级分析。 |
| `ecnu_plus` | `ECNU_PLUS_API_KEY` | 图片和扫描页 OCR。 |

生产环境通过环境变量提供密钥。开发环境支持本地 `deepseekv4.txt` 密钥容器，该文件已被 Git 忽略。两个 profile 不互相回退。详细配置见 [AI provider 说明](docs/ai-providers.md)。

变量清单见 [.env.example](.env.example)。项目不自动加载 `.env`。如果使用本地环境文件，由启动脚本或运行平台将变量注入进程。

## 部署与数据

生产环境使用单个 Waitress 进程，在前端配置 HTTPS 反向代理。完整步骤见 [部署指南](docs/deployment.md)。

主数据默认位于 `data/app.db`，上传原件位于 `data/uploads/`。可通过 `ESSAY_DATA_DIR` 指定数据目录。数据库、上传内容、备份、密钥和本地学校资料不属于仓库发布内容。

本项目面向约 1000 名学生的单校场景。当前只维护电脑浏览器，推荐最新版 Edge 或 Chrome，页面最小设计宽度为 `1200px`。不要通过增加服务进程数来扩容进程内队列。

## 仓库目录

```text
.
├── .github/              # CI、Issue 模板和 pull request 模板
├── docs/                 # 配置、部署、开发和页面流程文档
├── essay_platform/       # 后端模块、业务服务、存储和任务队列
├── static/               # 浏览器脚本、样式和第三方静态依赖
├── templates/            # Flask 页面模板
├── tools/                # 测试脚本和开发工具
├── data/.gitkeep         # 数据目录占位文件
├── app.py                # 本地启动入口
├── wsgi.py               # 生产 WSGI 入口
├── requirements.txt      # 运行依赖
└── requirements-dev.txt  # 开发与测试依赖
```

`testdata/` 仅保存本地学校资料，默认忽略。不要将真实学生名单或作文作为共享测试夹具。

## 开发与检查

安装开发依赖。

```bash
python -m pip install -r requirements-dev.txt
```

运行主要回归检查。

```bash
python tools/regression_test.py
```

更多检查见 [开发指南](docs/development.md)。[GitHub Actions](.github/workflows/ci.yml) 配置了 Windows、Linux，以及 Python 3.10 和 3.13 的检查。自动测试使用临时数据目录和假 AI，无需业务密钥。

## 文档与贡献

- [文档索引](docs/README.md) 汇总配置、部署和页面流程。
- [贡献指南](CONTRIBUTING.md) 说明开发环境、变更提交和检查要求。
- [安全问题报告](SECURITY.md) 说明私密报告方式和敏感数据处理要求。

## 许可证

本项目尚未声明项目许可证。公开可见不代表已授予开源使用、修改或再分发许可。第三方 Chart.js 的许可证保留在 [chart.LICENSE.md](static/vendor/chart.LICENSE.md)。
