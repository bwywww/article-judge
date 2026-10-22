# 网页 AI provider 边界

网页只使用两个运行时 profile：

| profile | 环境变量 | 用途 | 并发 |
|---|---|---|---|
| `article_judge` | `ARTICLE_JUDGE_API_KEY` | 作文批改、提升版、姓名识别、辅助切分、班级共性分析 | 全局批改池最多 4 路 |
| `ecnu_plus` | `ECNU_PLUS_API_KEY` | 图片和 PDF 扫描页 OCR、多模态连接测试 | 独立工作池固定 1 路 |

`ecnu_plus` 不继承 `article_judge` 的 base URL、模型或密钥。OCR 调用使用 ECNU OpenAI 兼容的多模态消息格式；管理员连接测试会在内存中生成写有 `TEST 123` 的最小图片，不上传学生材料。自动化测试必须 mock 该调用。

开发环境中两个 profile 保存在同一个受忽略的 `deepseekv4.txt` 密钥容器内，分别使用 `article_judge ...` 与 `ecnu_plus ...` 两行；读取时严格按 profile 隔离，禁止互相回退。生产环境改用同一部署密钥系统中的 `ARTICLE_JUDGE_API_KEY` 和 `ECNU_PLUS_API_KEY` 两个独立条目，不部署明文文件。

官方资料说明 `ecnu-plus` 支持图片理解，并建议避免高并发调用；本项目据此固定串行 OCR：[ECNU 模型说明](https://developer.ecnu.edu.cn/vitepress/llm/model.html)、[多模态接口](https://developer.ecnu.edu.cn/vitepress/llm/api/vision.html)。

配额在创建 OCR 任务和每个实际 OCR 页面调用前检查。配额中途用尽时任务进入可显式重试的 `error`，已经完成的页不会丢失。文字层充分的 PDF 页、DOCX、TXT 和 Markdown 只做本地解析，但仍通过统一的智能导入任务显示进度。

生产环境不得部署 `deepseekv4.txt`。`CODE_DEVELOP_API_KEY` 只属于本地开发委派工具，不参与网页请求。

## 目标地址限制

文本任务只允许 `https://api.deepseek.com` 和 `https://api.deepseek.com/v1` 作为 Base URL。OCR 固定使用 `https://chat.ecnu.edu.cn/open/api/v1`。网页设置、连接测试和实际任务发送前都校验地址，带密钥的请求不跟随重定向。HTTP、自定义域名、代理地址和其他路径被拒绝。存量配置若使用不受信任的地址，管理员需要将地址改为允许值，程序不会自动修改旧配置。

## 名单与作文的数据边界

学生名单导入只做本地规则检查，不调用 AI 审核，不发送姓名、学号或班级信息给模型。此前的 AI 附加审核已停用。

作文批改、姓名识别、作文切分、班级分析和 OCR 仍需将对应文本或图片发送给外部服务。作文与扫描件中可能包含个人信息。实际使用前确认学校授权和服务商的数据保留要求，避免在作文中填写与教学无关的敏感信息。
