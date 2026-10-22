# 三端导航矩阵（已并入正式自动机文档）

本文件原有内容已被 [三端网页自动机、路由与跨账户影响](web-state-machine.md) 取代。后续只维护 `web-state-machine.md`，以免按钮、路由和跨账户影响出现两份不一致的说明。

<!-- 以下旧内容保留在 Git 历史，不在当前版本继续维护。 -->

<!--

# 三端操作 → 跳转界面 → 跨账户影响矩阵

本文档整理 admin / 教师 / 学生三端**每个操作的跳转界面**以及**对其他账户的影响**,供开发维护参考,避免出现"操作后跳到非预期界面"或"跨账户影响不清晰"的问题。

## 阅读约定

- 三端渲染模型差异:
  - **老师端** = 功能导航 + 作业列表 + 作业详情(内部 tab:概览 / 名单与导入 / 批改结果 / 班级分析)。跳转集中在 `static/js/teacher.js` 的 `openAssignment` / `renderDetail` / `detailTab`。
  - **管理员端** = 固定侧边导航 + 单工作区 + 弹窗,无"详情内 tab",所有操作原地刷新当前 tab。
  - **学生端** = 三个独立页面入口(老师作业 / 自主练习 / 成长记录)+ 页面内切换,整页跳转在模板 `<a href>`。
- "刷新方式"列说明操作完成后前端如何更新:
  - `openAssignment(cur.id)` = 刷新当前作业(**保留当前 tab**,见下方规则)
  - `openAssignment(新id)` = 打开另一作业(**重算 tab**)
  - `renderDetail()` = 原地渲染,保留当前 tab
  - `startPolling()` = 轮询刷新,保留当前 tab

## 老师端 tab 保留规则

`openAssignment(id)`(static/js/teacher.js):

- `id === cur.id`(刷新当前作业)→ **保留当前 detailTab**
- `id !== cur.id`(打开其他作业)→ **重算**:有 queued/grading 作文进 `results`,否则进 `overview`

此规则让所有"保存后刷新当前作业"的操作停留在原 tab(通常为批改结果页),不再跳回概览。

---

## 一、老师端:操作 → 跳转矩阵

| 操作 | 入口 | 刷新方式 | 目标 tab | 跨账户影响 |
|---|---|---|---|---|
| 打开作业 | 列表 `li[data-id]` | `openAssignment(新id)` | 重算(有批改中→results,否则 overview) | 无 |
| 切换 tab | `[data-detail-tab]` | `renderDetail()` | 保留 | 无 |
| 新建作业 | `#btn-new-assignment` | `openAssignment(新id)` | overview | 无 |
| 编辑作业 | `#btn-edit` | `openAssignment(cur.id)` | 保留 | 改 classId 会改变学生可见班级 |
| 删除草稿 | `#btn-del` | 关闭详情,回列表 | — | 草稿对学生本就不可见 |
| 发布 / 撤回 / 关闭 / 重开 / 归档 | `#btn-publish` 等 | `openAssignment(cur.id)` | 保留 | 发布→学生立即可见可提交;归档→学生端列表消失;关闭→不可提交 |
| 清空作文 | `#btn-clear` | `detailTab="import"; renderDetail()` | import | 清空全部作文/提交/批改,学生回到未提交态 |
| 上传作文文件 | `#dz` / `#file-input` | `detailTab="import"` | import | 老师导入覆盖→学生端锁定不可改(需 override 确认) |
| 确认名单保存 | `#can-save` | `openAssignment(cur.id)` | 保留(results) | 见上,导入作文落库 |
| 开始批改 | `#bt-grade` | `detailTab="results"` + 轮询 | results | 学生端显示排队/批改中 chip |
| 全部 / 单篇重批 | `#bt-regrade-all` / `.regrade` | `startPolling()` | results | 已发布历史保留,新结果待再发布 |
| 批量 / 单篇发布结果 | `#bt-publish-results` / `.publish-result` | `openAssignment(cur.id)` | 保留(results) | 学生端立即见结果 + 生成不可变 history 快照 |
| 编辑批改结果 | 详情弹窗 `#fb-edit`→`#fe-save` | `openAssignment(cur.id)` | 保留(results) | wasPublished 时学生仍见旧版直到再次发布 |
| 全部 / 单人开放重交 | `#bt-reopen-all` / `.reopen` | `openAssignment(cur.id)` | 保留(results) | 学生可覆盖作文 |
| 退回重交 | `.return-submission` | `openAssignment(cur.id)` | 保留(results) | 学生收到退回评语、可重交 |
| 查看版本时间线 / 学生趋势 | `.versions` / `.student-trend` | 弹窗 | — | 无 |
| 导出 PDF / XLSX | `#bt-export` / `#bt-completion` | 无跳转 | — | 无 |
| 克隆作业 | `#btn-clone` | `openAssignment(新id)` | overview | 仅复制题目/要求/标准/字数 |
| AI 汇总共性问题 / 编辑 | `#insight-generate` / `#insight-edit` | `renderDetail()` | 保留 | 无(仅老师可见) |
| 保存优秀作文名单 | `#showcase-save` | `renderDetail()` | 保留 | 发布结果后本班学生可见名单+小红花 |
| 名单行操作(绑定/拆分等) | candidate-row | `renderDetail()` | import | 未保存前仅前端缓存 |

---

## 二、管理员端:操作 → 跳转矩阵

管理员端无详情内 tab,所有操作 = 弹窗或原地刷新当前 tab(`renderAdmin()`)。

| 操作 | 刷新行为 | 跨账户影响 |
|---|---|---|
| 模块启停 | `renderAdmin()` | 立即影响所有端入口与接口:页面级 `data-feature` 隐藏导航,`feature_required` 对 disabled 返回 503,依赖模块联动停用 |
| AI 连接参数 / 任务路由 / 测试 | 设置弹窗 + 保存重渲染 | 配置错误影响全体批改 / OCR 任务 |
| 学年结转 | 弹窗 + `renderAdmin()` | 冻结旧作业/旧班级、建新班级、毕业班停用;阻塞条件:有草稿/批改任务/待发布结果 |
| 备份 / 恢复 | 弹窗 | 恢复 = 重启后原子替换数据库,全量回滚影响所有账户 |
| 新建 / 编辑老师 | 弹窗 + `renderAdmin()` | 任教班级决定其作业可见范围 |
| 重置密码 | 临时密码弹窗 | 该账号现有会话立即失效 |
| 删除老师 | `renderAdmin()` | 有业务记录→停用(数据保留),无记录→真删除;自动备份 |
| 移交老师作业 | 确认 toast,不刷新 | 全部作业 `created_by` 移交,原老师不再拥有 |
| 学生转学 / 毕业 / 恢复 | 弹窗 + `renderAdmin()` | 停用并撤销会话,历史保留;毕业/转学后不再见对应班级作业 |
| 学生 Excel / 老师 Excel 导入 | 预览 + 确认 + `renderAdmin()` | 创建/更新账号,自动分班,临时密码 |
| 班级增删改 / 加老师学生 | `renderAdmin()` | 决定老师可管理班级、学生可见作业;有作业的班级不能删 |
| 审计日志 | `renderAdmin()` | 无 |

---

## 三、学生端:操作 → 跳转矩阵

学生端页面内切换,无老师端那种 tab 状态。

| 操作 | 行为 | 跨账户影响 |
|---|---|---|
| 切换作业视图(待完成/本班记录/历史学年) | `renderAssignmentList()` | 历史学年来自 archive(仅 published/closed) |
| 选择作业 | `pickAssignment` | 可提交→显示提交卡;锁定→占位;退回→显示评语+重交截止 |
| 提交作文并批改 | 锁定提交卡 + 轮询 | 老师端 essay 出现,结果 pending_review 等发布 |
| 上传文件载入正文 | 前端解析 | 无 |
| 查看结果 / 润色 / 导出 | 原地重渲染 / 下载 | 无 |
| 历史记录 tab / 详情 / 导出 / 版本时间线 | 分页加载 / 内联 | 展示老师发布时的不可变快照 |
| 自主练习提交 | 结果即时出现 | 不进老师端作业,写入该生自己的 history |

---

## 四、跨账户影响汇总

| 动作 | 影响谁 | 学生端何时可见 |
|---|---|---|
| 老师发布成绩 | 该生 | 立即见结果 + 不可变 history 快照;老师之后改反馈未再发布则仍见旧快照 |
| 老师退回重交 | 该生 | 收到退回评语、可重交,历史时间线可回看 |
| 老师重新开放(全班/单人/作业) | 该生 | 可覆盖提交 |
| 老师清空作文 | 全体学生 | 回到未提交态 |
| 老师归档 | 全体学生 | 作业从本班记录消失,历史仍可看 |
| 老师关闭 | 全体学生 | 不可提交,已发布结果仍可见 |
| 老师导入覆盖学生提交 | 该生 | 锁定不可改,需老师 reopen 才可重交 |
| 老师改反馈(wasPublished) | 该生 | 仍见旧快照直到再次发布 |
| admin 学年结转 | 全体师生 | 旧班级/作业冻结进历史学年,毕业班停用 |
| admin 关闭模块 | 对应端所有人 | 页面隐藏 + 接口 503 |
| admin 停用/删除账号 | 该账号 | 会话失效;老师有记录→停用,学生有关联→禁删(409 建议停用) |
| admin 重置密码 | 该账号 | 现有会话立即失效 |
| admin 移交作业 | 目标老师 | 作业出现在目标老师端 |

## 模块启停对端的可见性

| 模块 | 老师端影响 | 学生端影响 |
|---|---|---|
| imports | 上传/OCR/名单按钮隐藏,接口 503 | 上传按钮隐藏 |
| grading | 开始批改/重批 503 | 提交 503 |
| review | 发布/退回/反馈编辑 503 | 等待发布 |
| assignments | 作业导航隐藏 | 老师作业导航隐藏,联动停用其他模块 |
| practice | 无 | 自主练习导航隐藏 |
| reporting | 提交记录导航隐藏 | 成长记录导航隐藏 |
| academic_years | 无 | 历史学年不加载 |
| revisions | 退回/版本时间线 | 退回重交 |
| analytics / showcase | 分析 tab / 优秀作文区 | 成长曲线 / 优秀作文 |
| ocr | 图片上传前端拦截 + 后端 503,文字版仍可用 | 图片/扫描件上传阻断 |
| operations | 无 | 无(admin AI 管理导航隐藏) |

## 已修复问题

- **老师编辑批改结果保存后跳回概览页**:根因 `openAssignment` 无条件重算 `detailTab`,已改为"刷新当前作业保留当前 tab"。修复后 results tab 下保存批改/发布/退回等操作都停留在批改结果页。

## 维护约定

1. 新增任何前端操作,必须在本矩阵登记一行(操作 / 入口 / 刷新方式 / 跨账户影响)。
2. `grep -n "openAssignment(" static/js/teacher.js` 的调用点应与第一节逐一对应。
3. 修改 `detailTab` 的地方需显式说明意图(保留 / 重算),避免隐式跳转回归。

-->
