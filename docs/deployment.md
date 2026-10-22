# 部署指南

## 准备数据目录

本项目使用单校、单服务器和单个 Python 服务进程。生产运行依赖已有的有效数据库。

默认数据库为仓库根目录的 `data/app.db`。如需将运行数据与代码分开，通过 `ESSAY_DATA_DIR` 设置专用数据目录。上传原件位于数据目录的 `uploads/`。

新克隆的仓库不包含学校数据。使用已有备份准备数据库和上传文件。首次演示的初始化步骤见 [项目首页](../README.md#首次在本地运行)。不要对生产数据目录启用 `ESSAY_BOOTSTRAP_DEMO`。

数据库缺失时，普通启动拒绝创建空库。默认路径缺失时，应用可安全迁移旧版 `essay_platform/data/app.db`。候选数据库冲突或完整性异常时，应用拒绝启动。不要通过创建空数据库绕过检查。

## 配置生产环境

安装运行依赖。

```bash
python -m pip install -r requirements.txt
```

以下示例使用 Windows PowerShell。替换占位值后执行。

```powershell
$env:APP_ENV="production"
$env:ARTICLE_JUDGE_API_KEY="替换为文本批改密钥"
$env:ECNU_PLUS_API_KEY="替换为 OCR 密钥"
$env:ADMIN_PASSWORD="替换为管理员强密码"
```

如需自定义数据目录，设置绝对路径。

```powershell
$env:ESSAY_DATA_DIR="C:\essay-platform-data"
```

Linux 上通过进程管理器、容器或 shell 的 `export` 设置同名变量。应用不自动加载 `.env`。生产环境不要部署明文密钥容器 `deepseekv4.txt`。

AI profile 的端点、模型和边界见 [AI provider 说明](ai-providers.md)。文本接口仅允许官方 HTTPS 地址，已有代理或自定义地址会被拒绝，需要管理员修改配置。服务器不会自动改写原有地址。

数据库、上传原件和备份保存在项目之外，不随代码仓库发布。部署时应通过 `ESSAY_DATA_DIR` 指向受访问控制的学校数据目录。

## 启动服务

在仓库根目录运行单个 Waitress 进程。

```bash
waitress-serve --listen=127.0.0.1:8080 --threads=8 wsgi:app
```

启动日志应显示所用数据库位置。确认位置是预期的学校数据目录。

在 Waitress 前配置 HTTPS 反向代理、防火墙和访问控制。由操作系统的进程管理工具负责启动、重启和日志收集。

不要使用 Flask 开发服务器承载生产请求。批改调度器使用进程内的全局四路队列，OCR 调度器使用单路队列，因此生产环境只运行一个服务进程。

## 备份与恢复

SQLite 使用 WAL、外键、事务和 `busy_timeout`。上传原件保存在文件系统，SQLite 保存元数据。数据库备份不能替代上传文件备份。

Schema 升级、学年结转和部分批量操作前，应用自动备份。管理员可在“学年与恢复”中预检备份。恢复在服务重启时执行，并在恢复前再次备份当前库。

将数据库、上传文件和备份纳入学校的数据保管方案。保留目录权限和恢复演练记录。不要把运行数据上传到 GitHub。

## 发布仓库前检查

从包含 `app.py` 和 `.git/` 的目录提交仓库，避免再包一层项目目录。

检查待提交文件。

```bash
git status --short
```

检查待提交补丁。

```bash
git diff --cached --stat
```

`.gitignore` 不会移除已跟踪文件，也不会清除历史。公开前确认当前文件和 Git 历史不含 API 密钥、真实学生资料、数据库或上传原件。密钥泄露后先撤销并重新生成。

仓库尚未声明项目许可证。由项目所有者确认授权方式后再添加 `LICENSE`。
