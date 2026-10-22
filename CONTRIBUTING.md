# 贡献指南

## 准备开发环境

使用 Python 3.10 或更高版本和 Node.js 22。Node.js 用于检查浏览器 JavaScript 语法。

在仓库根目录创建虚拟环境。

```bash
python -m venv .venv
```

Windows PowerShell 激活命令如下。

```powershell
.venv\Scripts\Activate.ps1
```

macOS 或 Linux 激活命令如下。

```bash
source .venv/bin/activate
```

安装运行和测试依赖。

```bash
python -m pip install -r requirements-dev.txt
```

首次运行见 [项目首页](README.md)。测试入口和模块约束见 [开发指南](docs/development.md)。

## 报告问题

功能缺陷使用缺陷报告模板，并提供脱敏的复现步骤。功能建议说明使用角色、现有问题和验收条件。

安全漏洞按 [安全问题报告](SECURITY.md) 私下提交。

## 提交变更

在独立分支中完成一个目的明确的修改。保持现有 Python 和浏览器原生 ES Module 的代码风格。新增行为需要相应的验证，并同步更新文档。

提交前运行 [开发指南中的检查](docs/development.md#运行检查)，再检查补丁。

```bash
git diff --check
```

Pull request 说明解决的问题、修改后的行为、实际验证结果和尚未验证的内容。存储结构变更需要说明迁移与恢复方式。

## 保护本地数据

测试只使用临时目录。不要将测试指向学校正在使用的 `data/` 或旧版 `essay_platform/data/`。

不要提交数据库、备份、上传原件、学生名单、真实作文、个人配置或 API 密钥。`testdata/` 是本地资料目录，默认不纳入 Git。需要共享测试夹具时，使用人工构造的脱敏数据，并明确记录来源。

`.gitignore` 只影响未跟踪文件。检查待提交内容，确认没有已跟踪的敏感文件。

```bash
git diff --cached --stat
```

## 许可证

本仓库尚未声明项目许可证。公开可见不代表已授予开源使用、修改或再分发许可。提交或复用代码前，与仓库所有者确认授权方式。第三方组件遵循各自的许可证。
