/* ============ 管理员页面逻辑 ============ */
import {
  api, applyFeatureVisibility, closeModal, formatLocalDateTime, h, openModal,
  openSettings, pageInit, renderAiProfileStatus, setModalDismissible, toast,
} from "./shared/index.js";
import { aiTaskName, numberText } from "./admin/ai.js";
import { renderClassPanel } from "./admin/classes.js";
import { classChips, permissionName, studentLabel } from "./admin/directory.js";
import { defaultAcademicYearStart } from "./admin/imports.js";
import { studentImportPreviewView, teacherImportPreviewView } from "./admin/import-preview.js";
import { moduleStatusClass, moduleStatusName } from "./admin/modules.js";

let adminUsers = [];
let adminClasses = [];
let adminAuditItems = [];
let adminModules = [];
let adminAiRoutes = [];
let adminAiProfiles = {};
let adminAiUsage = { items: [], total: 0, summary: {} };
let adminAcademicYears = [];
let adminBackups = [];
let adminSchoolSettings = { settings: {}, quota: {} };
const classCollator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
const cnNumbers = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
const labelNumber = value => {
  const text = String(value || "");
  const digits = text.match(/\d+/);
  if (digits) return Number(digits[0]);
  if (text.includes("十一")) return 11;
  if (text.includes("十二")) return 12;
  return Object.entries(cnNumbers).find(([key]) => text.includes(key))?.[1] ?? 10000;
};
const gradeOrder = value => {
  const text = String(value || "");
  const number = labelNumber(text);
  if (text.includes("高")) return [2, number];
  if (text.includes("初")) return [1, number];
  if ([7, 8, 9].includes(number)) return [1, number - 6];
  return number === 10000 ? [9, number] : [0, number];
};
const sortedClasses = items => [...(items || [])].sort((a, b) =>
  gradeOrder(a.grade)[0] - gradeOrder(b.grade)[0]
  || gradeOrder(a.grade)[1] - gradeOrder(b.grade)[1]
  || labelNumber(a.klass) - labelNumber(b.klass)
  || classCollator.compare(a.klass || "", b.klass || "")
  || classCollator.compare(a.name || "", b.name || ""));
let adminTab = localStorage.getItem("adminTab") || "teachers";
if (!["teachers", "students", "classes", "school", "audit", "modules", "ai"].includes(adminTab)) adminTab = "teachers";
let adminPage = 1;
let adminPageSize = 30;
let adminTotal = 0;
let adminQuery = "";
let teacherSelection = new Set();

pageInit(["admin"], async () => {
  const tabFeatures = { teachers: "directory", students: "directory", classes: "directory",
    school: "academic_years", ai: "operations" };
  const selectedFeature = tabFeatures[adminTab];
  if (selectedFeature && window.features[selectedFeature] && window.features[selectedFeature].status === "disabled") {
    adminTab = "modules";
    localStorage.setItem("adminTab", adminTab);
  }
  document.querySelectorAll(".admin-nav").forEach(button => {
    button.addEventListener("click", async () => {
      adminTab = button.dataset.tab;
      adminPage = 1;
      adminQuery = "";
      teacherSelection.clear();
      localStorage.setItem("adminTab", adminTab);
      await renderAdmin();
    });
  });
  window.onConfigSaved = async () => {
    const cfg = await api("/api/config");
    window.hasKey = cfg.has_key;
    refreshKeyBanner();
    if (adminTab === "ai") await renderAdmin();
  };
  await renderAdmin();
});

async function loadAdminData() {
  if (adminTab === "modules") {
    const response = await api("/api/admin/modules");
    adminModules = response.modules || [];
    adminTotal = adminModules.length;
    return;
  }
  if (adminTab === "ai") {
    const [routes, usage] = await Promise.all([
      api("/api/admin/ai/routes"),
      api("/api/admin/ai/usage?page=1&pageSize=20"),
    ]);
    adminAiRoutes = routes.routes || [];
    adminAiProfiles = routes.profiles || {};
    adminAiUsage = usage || { items: [], total: 0, summary: {} };
    adminTotal = adminAiUsage.total || 0;
    return;
  }
  if (adminTab === "classes") {
    const response = await api("/api/classes");
    adminClasses = sortedClasses(response.classes || []);
    adminTotal = adminClasses.length;
    return;
  }
  if (adminTab === "school") {
    const [years, backups, settings] = await Promise.all([
      api("/api/admin/academic-years"), api("/api/admin/recovery/backups"),
      api("/api/admin/school-settings"),
    ]);
    adminAcademicYears = years.academicYears || [];
    adminBackups = backups.backups || [];
    adminSchoolSettings = settings || { settings: {}, quota: {} };
    adminTotal = adminAcademicYears.length;
    return;
  }
  if (adminTab === "audit") {
    const query = new URLSearchParams({
      q: adminQuery, page: String(adminPage), pageSize: String(adminPageSize),
    });
    const response = await api(`/api/admin/audit?${query}`);
    adminAuditItems = response.items || [];
    adminTotal = response.total || 0;
    return;
  }
  const role = adminTab === "teachers" ? "teacher" : "student";
  const query = new URLSearchParams({
    role,
    q: adminQuery,
    page: String(adminPage),
    pageSize: String(adminPageSize),
  });
  const [response, classResponse] = await Promise.all([
    api(`/api/admin/users?${query}`),
    api("/api/classes"),
  ]);
  adminUsers = response.users || [];
  adminClasses = sortedClasses(classResponse.classes || []);
  adminTotal = response.total || 0;
}

async function renderAdmin() {
  await loadAdminData();
  document.querySelectorAll(".admin-nav").forEach(button => {
    button.classList.toggle("active", button.dataset.tab === adminTab);
  });
  const root = document.getElementById("admin-root");
  root.innerHTML = `
    ${adminTab === "teachers" ? renderTeacherPanel(adminUsers) : ""}
    ${adminTab === "students" ? renderStudentPanel(adminUsers) : ""}
    ${adminTab === "classes" ? renderClassPanel(adminClasses) : ""}
    ${adminTab === "school" ? renderSchoolPanel() : ""}
    ${adminTab === "audit" ? renderAuditPanel() : ""}
    ${adminTab === "modules" ? renderModulePanel() : ""}
    ${adminTab === "ai" ? renderAiPanel() : ""}`;
  applyFeatureVisibility(root);
  bindAdminEvents();
}

function renderTeacherPanel(teachers) {
  return `
    <div class="page-heading compact-heading">
      <div><h1>老师管理</h1><p>工号用于登录；老师可以任教多个已建立班级。</p></div>
      <div class="ops"><button class="btn" id="bt-import-teachers">Excel 导入</button>
        <button class="btn danger" id="bt-delete-teachers" ${teacherSelection.size ? "" : "disabled"}>批量删除</button>
        <button class="btn primary" id="bt-new-teacher">新建老师</button></div>
    </div>
    <section class="data-surface">
      <div class="data-toolbar">
        <div class="search-group"><input class="input" id="teacher-query" value="${h(adminQuery)}" placeholder="搜索姓名、工号、学科或班级">
          <button class="btn" id="teacher-search">搜索</button></div>
        <span class="data-count">共 ${adminTotal} 位老师</span>
      </div>
      <div class="table-wrap">
      <table class="table responsive-table">
        <thead><tr><th><input type="checkbox" id="teacher-select-all" aria-label="选择当前页全部老师"></th><th>姓名</th><th>工号</th><th>学科</th><th>任教班级</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="teacher-rows">${renderTeacherRows(teachers)}</tbody>
      </table></div>
      ${renderAdminPagination()}
    </section>`;
}

function renderTeacherRows(teachers) {
  return teachers.map(u => `
    <tr>
      <td data-label="选择"><input class="teacher-select" type="checkbox" data-username="${h(u.username)}" ${teacherSelection.has(u.username) ? "checked" : ""} aria-label="选择 ${h(u.display_name)}"></td>
      <td data-label="姓名"><b>${h(u.display_name)}</b></td>
      <td data-label="工号">${h(u.employeeNo || u.username)}</td>
      <td data-label="学科">${h(u.subject || "—")}</td>
      <td data-label="任教班级">${classChips(u)}</td>
      <td data-label="状态"><span class="chip ${u.active ? "green" : "gray"}">${u.active ? "正常" : "已停用"}</span></td>
      <td data-label="操作">
        <button class="btn small ghost user-edit" data-username="${h(u.username)}">编辑</button>
        <button class="btn small ghost user-reset" data-username="${h(u.username)}">重置密码</button>
        <button class="btn small ghost teacher-transfer" data-username="${h(u.username)}">移交作业</button>
        ${u.active ? `<button class="btn small danger user-del" data-username="${h(u.username)}">删除</button>`
          : `<button class="btn small primary user-enable" data-username="${h(u.username)}">重新启用</button>`}
      </td>
    </tr>`).join("") || `<tr><td colspan="7" class="empty" style="padding:24px">没有匹配老师</td></tr>`;
}

function renderStudentPanel(students) {
  return `
    <div class="page-heading compact-heading">
      <div><h1>学生管理</h1><p>学号是唯一身份；班级归属通过班级页面统一分配。</p></div>
      <div class="ops"><button class="btn" id="bt-import-students" data-feature-write="imports">Excel 导入</button>
        <button class="btn primary" id="bt-new-student">新建学生</button></div>
    </div>
    <section class="data-surface">
      <div class="data-toolbar">
        <div class="search-group"><input class="input" id="student-query" value="${h(adminQuery)}" placeholder="搜索姓名、学号、用户名或班级">
          <button class="btn" id="student-search">搜索</button></div>
        <span class="data-count">共 ${adminTotal} 名学生</span>
      </div>
      <div class="table-wrap"><table class="table responsive-table">
        <thead><tr><th>姓名</th><th>学号</th><th>用户名</th><th>年级班级</th><th>所在班级</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="student-rows">${renderStudentRows(students)}</tbody>
      </table></div>
      ${renderAdminPagination()}
    </section>`;
}

function renderStudentRows(students) {
  return students.map(u => `
    <tr>
      <td data-label="姓名"><b>${h(u.display_name)}</b></td>
      <td data-label="学号">${h(u.studentNo || u.username)}</td>
      <td data-label="登录账号">${h(u.username)}</td>
      <td data-label="年级 / 班级">${h(u.grade || "—")}${u.klass ? ` / ${h(u.klass)}` : ""}</td>
      <td data-label="行政班">${classChips(u)}</td>
      <td data-label="状态"><span class="chip ${u.schoolStatus === "active" ? "green" : "gray"}">${u.schoolStatus === "graduated" ? "已毕业" : u.schoolStatus === "transferred" ? "已转学" : "在校"}</span></td>
      <td data-label="操作">
        <button class="btn small ghost user-edit" data-username="${h(u.username)}">编辑</button>
        <button class="btn small ghost user-reset" data-username="${h(u.username)}">重置密码</button>
        ${u.schoolStatus === "active" ? `<button class="btn small ghost student-status" data-username="${h(u.username)}" data-status="transferred">转学</button>
          <button class="btn small ghost student-status" data-username="${h(u.username)}" data-status="graduated">毕业</button>`
          : `<button class="btn small primary student-status" data-username="${h(u.username)}" data-status="active">恢复在校</button>`}
      </td>
    </tr>`).join("") || `<tr><td colspan="7" class="empty" style="padding:24px">没有匹配学生</td></tr>`;
}

function renderAdminPagination() {
  const pages = Math.max(1, Math.ceil(adminTotal / adminPageSize));
  return `<div class="pagination">
    <button class="btn small ghost admin-prev" ${adminPage <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${adminPage} / ${pages} 页</span>
    <button class="btn small ghost admin-next" ${adminPage >= pages ? "disabled" : ""}>下一页</button>
  </div>`;
}

function auditName(type) {
  const names = {
    login_succeeded: "登录成功", login_failed: "登录失败", login_blocked: "登录锁定",
    logout: "退出登录", password_changed: "修改密码", password_change_failed: "修改密码失败", password_reset: "重置密码",
    user_created: "创建账号", user_updated: "修改账号", user_deleted: "删除账号",
    teacher_created: "创建老师", teacher_updated: "修改老师",
    teacher_removed: "删除或停用老师", teachers_batch_removed: "批量删除或停用老师",
    teacher_import_confirmed: "确认教师导入",
    class_created: "创建班级", class_updated: "修改班级", class_deleted: "删除班级",
    class_student_assigned: "学生分班", class_student_removed: "移出学生",
    class_teacher_assigned: "分配老师", class_teacher_removed: "移出老师",
    student_import_confirmed: "确认学生导入", ai_config_updated: "修改 AI 设置",
    ai_config_test_succeeded: "AI 测试成功", ai_config_test_failed: "AI 测试失败",
    admin_password_environment_override: "环境变量更新管理员密码",
    assignment_created: "创建作业", assignment_updated: "修改作业",
    assignment_publish: "发布作业", assignment_unpublish: "撤回作业",
    assignment_close: "关闭作业", assignment_reopen: "重新开放作业",
    assignment_archive: "归档作业", assignment_auto_closed: "截止时间自动关闭作业",
    assignment_deleted: "删除作业", assignment_essays_cleared: "清空作业作文",
    assignment_result_published: "发布批改结果", assignment_results_published: "批量发布批改结果",
    assignment_feedback_edited: "人工修改批改结果",
    assignment_submission_reopened: "开放学生重交", assignment_submissions_reopened: "批量开放重交",
  };
  return names[type] || type;
}

function renderAuditPanel() {
  return `
    <div class="page-heading compact-heading">
      <div><h1>审计日志</h1><p>账号、班级、接口配置和登录安全操作记录。</p></div>
    </div>
    <section class="data-surface">
      <div class="data-toolbar">
        <div class="search-group"><input class="input" id="audit-query" value="${h(adminQuery)}" placeholder="搜索操作人、事件或目标">
          <button class="btn" id="audit-search">搜索</button></div>
        <span class="data-count">共 ${adminTotal} 条</span>
      </div>
      <div class="table-wrap"><table class="table responsive-table">
        <thead><tr><th>时间</th><th>事件</th><th>操作人</th><th>目标</th><th>来源 IP</th><th>摘要</th></tr></thead>
        <tbody>${adminAuditItems.map(item => `<tr>
          <td data-label="时间">${h(formatLocalDateTime(item.createdAt))}</td>
          <td data-label="事件"><b>${h(auditName(item.type))}</b></td>
          <td data-label="操作人">${h(item.actorUsername || "系统")}</td>
          <td data-label="目标">${h([item.targetType, item.targetId].filter(Boolean).join(" / ") || "—")}</td>
          <td data-label="来源 IP">${h(item.ipAddress || "—")}</td>
          <td data-label="摘要"><span class="audit-detail">${h(Object.keys(item.detail || {}).length ? JSON.stringify(item.detail) : "—")}</span></td>
        </tr>`).join("") || '<tr><td colspan="6" class="empty compact-empty">暂无审计记录</td></tr>'}</tbody>
      </table></div>
      ${renderAdminPagination()}
    </section>`;
}

function renderModuleRows(modules) {
  return modules.map(item => {
    const dependencies = (item.dependencies || []).join("、") || "—";
    const action = item.implemented === false
      ? '<span class="module-core">尚未实现</span>'
      : item.toggleable
      ? `<button class="btn small ${item.status === "enabled" ? "danger" : "primary"} module-state"
          data-key="${h(item.key)}" data-state="${item.status === "enabled" ? "disabled" : "enabled"}"
          ${item.status === "draining" ? "disabled" : ""}>${item.status === "enabled" ? "停用" : item.status === "draining" ? "等待任务" : "启用"}</button>`
      : `<span class="module-core">核心模块</span>`;
    return `<tr>
      <td data-label="模块"><b>${h(item.name)}</b><small class="table-subtext">${h(item.key)}</small></td>
      <td data-label="作用">${h(item.description)}</td>
      <td data-label="依赖">${h(dependencies)}</td>
      <td data-label="状态"><span class="chip ${moduleStatusClass(item.status)}">${item.implemented === false ? "尚未实现" : moduleStatusName(item.status)}</span>
        ${item.activeJobs ? `<small class="table-subtext">${item.activeJobs} 个任务处理中</small>` : ""}</td>
      <td data-label="操作">${action}</td>
    </tr>`;
  }).join("");
}

function renderModulePanel() {
  const core = adminModules.filter(item => !item.toggleable);
  const business = adminModules.filter(item => item.toggleable);
  return `
    <div class="page-heading compact-heading">
      <div><h1>功能模块</h1><p>停用功能不会删除数据；有任务运行时先进入排空状态。</p></div>
      <button class="btn ghost" id="module-refresh">刷新状态</button>
    </div>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>业务模块</b><span>可按依赖关系启用或停用</span></div>
      <div class="table-wrap"><table class="table responsive-table module-table">
        <thead><tr><th>模块</th><th>作用</th><th>依赖</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${renderModuleRows(business)}</tbody>
      </table></div>
    </section>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>核心模块</b><span>保证登录、权限、数据和任务恢复，不可停用</span></div>
      <div class="table-wrap"><table class="table responsive-table module-table">
        <thead><tr><th>模块</th><th>作用</th><th>依赖</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${renderModuleRows(core)}</tbody>
      </table></div>
    </section>`;
}

function renderSchoolPanel() {
  const active = adminAcademicYears.find(item => item.status === "active");
  const settings = adminSchoolSettings.settings || {};
  const quota = adminSchoolSettings.quota || {};
  return `
    <div class="page-heading compact-heading"><div><h1>学年与恢复</h1>
      <p>学年结转冻结旧班级和旧作业；恢复操作在重启后执行。</p></div>
      <button class="btn primary" id="academic-rollover" ${active ? "" : "disabled"}>新学年预检</button></div>
    <section class="data-surface admin-section">
      <div class="section-strip"><div><b>学校设置与 AI 软限额</b><span>0 表示不限额；达到 80% 提示，达到 100% 暂停新任务</span></div>
        <button class="btn primary" id="school-settings-save">保存</button></div>
      <div class="form-grid school-settings-grid">
        <label class="field"><span>学校名称</span><input class="input" id="school-name" maxlength="100" value="${h(settings.school_name || "")}"></label>
        <label class="field"><span>学年说明</span><input class="input" id="school-year-note" maxlength="500" value="${h(settings.school_academic_year_note || "")}"></label>
        <label class="field"><span>每日 Token 上限</span><input class="input" id="daily-token-limit" type="number" min="0" value="${h(settings.ai_daily_token_limit || 0)}">
          <small>今日已用 ${numberText(quota.dailyTokens || 0)}</small></label>
        <label class="field"><span>每月 Token 上限</span><input class="input" id="monthly-token-limit" type="number" min="0" value="${h(settings.ai_monthly_token_limit || 0)}">
          <small>本月已用 ${numberText(quota.monthlyTokens || 0)}</small></label>
      </div>
      ${quota.blocked ? '<div class="banner-key plain danger-text">当前已达到限额，新批改任务已暂停。</div>'
        : quota.warning ? '<div class="banner-key plain">AI 用量已超过限额的 80%，请留意。</div>' : ""}
    </section>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>学年档案</b><span>当前 ${h(active?.label || "未建立")}</span></div>
      <div class="table-wrap"><table class="table responsive-table"><thead><tr><th>学年</th><th>状态</th><th>建立时间</th><th>关闭时间</th></tr></thead>
        <tbody>${adminAcademicYears.map(item => `<tr><td data-label="学年"><b>${h(item.label)}</b></td>
          <td data-label="状态"><span class="chip ${item.status === "active" ? "green" : "gray"}">${item.status === "active" ? "当前" : "已归档"}</span></td>
          <td data-label="建立时间">${h(formatLocalDateTime(item.createdAt))}</td><td data-label="关闭时间">${h(formatLocalDateTime(item.closedAt) || "—")}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>
    <section class="data-surface admin-section">
      <div class="section-strip"><div><b>备份恢复中心</b><span>恢复前先预检，确认后需重启服务</span></div>
        <button class="btn" id="backup-create">立即备份</button></div>
      <div class="table-wrap"><table class="table responsive-table"><thead><tr><th>备份</th><th>时间</th><th>大小</th><th>操作</th></tr></thead>
        <tbody>${adminBackups.map(item => `<tr><td data-label="备份"><b>${h(item.id)}</b></td>
          <td data-label="时间">${h(formatLocalDateTime(item.modifiedAt))}</td>
          <td data-label="大小">${(Number(item.size || 0) / 1024 / 1024).toFixed(1)} MB</td>
          <td data-label="操作"><button class="btn small ghost backup-preview" data-id="${h(item.id)}">预检</button></td></tr>`).join("") || '<tr><td colspan="4" class="empty compact-empty">暂无 SQLite 备份</td></tr>'}</tbody>
      </table></div>
    </section>`;
}

function renderAiPanel() {
  const summary = adminAiUsage.summary || {};
  return `
    <div class="page-heading compact-heading">
      <div><h1>AI 管理</h1><p>管理作文批改所使用的接口、模型路由与用量。</p></div>
      <div class="ops"><button class="btn ghost" id="ai-connection">连接参数</button>
        <button class="btn" id="ai-test">测试作文业务配置</button></div>
    </div>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>密钥托管状态</b><span>仅显示状态，不显示密钥内容</span></div>
      <div class="config-status-list admin-profile-list">${renderAiProfileStatus(adminAiProfiles)}</div>
    </section>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>业务任务路由</b><span>作文批改用 article_judge，OCR 识别用 ecnu_plus</span></div>
      <div class="table-wrap"><table class="table responsive-table ai-route-table">
        <thead><tr><th>任务</th><th>配置</th><th>模型</th><th>JSON</th><th>思考</th><th>最大输出</th></tr></thead>
        <tbody>${adminAiRoutes.map(route => `<tr class="ai-route-row" data-task="${h(route.taskType)}">
          <td data-label="任务"><b>${h(aiTaskName(route.taskType))}</b><small class="table-subtext">${h(route.taskType)}</small></td>
          <td data-label="配置">${h(route.profile)}</td>
          <td data-label="模型"><input class="input compact ai-route-model" value="${h(route.model)}" maxlength="100"></td>
          <td data-label="JSON"><input class="ai-route-json" type="checkbox" ${route.jsonMode ? "checked" : ""} aria-label="启用 JSON 输出"></td>
          <td data-label="思考"><input class="ai-route-thinking" type="checkbox" ${route.thinking ? "checked" : ""} aria-label="启用思考模式"></td>
          <td data-label="最大输出"><input class="input compact ai-route-tokens" type="number" min="64" max="12000" value="${h(route.maxTokens)}"></td>
        </tr>`).join("")}</tbody>
      </table></div>
      <div class="panel-actions"><button class="btn primary" id="ai-routes-save">保存任务路由</button></div>
    </section>
    <section class="data-surface admin-section">
      <div class="section-strip"><b>AI 用量</b><span>不记录作文正文和密钥</span></div>
      <div class="usage-summary">
        <div><span>请求</span><b>${numberText(summary.requests)}</b></div>
        <div><span>输入 Token</span><b>${numberText(summary.promptTokens)}</b></div>
        <div><span>输出 Token</span><b>${numberText(summary.completionTokens)}</b></div>
        <div><span>缓存命中</span><b>${numberText(summary.cacheHitTokens)}</b></div>
        <div><span>失败</span><b>${numberText(summary.failures)}</b></div>
      </div>
      <div class="table-wrap"><table class="table responsive-table ai-usage-table">
        <thead><tr><th>时间</th><th>来源</th><th>任务</th><th>模型</th><th>输入 / 输出</th><th>耗时</th><th>状态</th></tr></thead>
        <tbody>${(adminAiUsage.items || []).map(item => `<tr>
          <td data-label="时间">${h(formatLocalDateTime(item.createdAt))}</td>
          <td data-label="来源">${item.profile === "ecnu_plus" ? "OCR 识别" : "作文业务"}</td>
          <td data-label="任务">${h(aiTaskName(item.taskType))}</td>
          <td data-label="模型">${h(item.model)}</td>
          <td data-label="输入 / 输出">${numberText(item.promptTokens)} / ${numberText(item.completionTokens)}</td>
          <td data-label="耗时">${Number(item.durationSeconds || 0).toFixed(1)} 秒</td>
          <td data-label="状态"><span class="chip ${item.status === "ok" ? "green" : "red"}">${item.status === "ok" ? "成功" : h(item.errorType || "失败")}</span></td>
        </tr>`).join("") || '<tr><td colspan="7" class="empty compact-empty">暂无 AI 调用记录</td></tr>'}</tbody>
      </table></div>
    </section>`;
}

function bindAdminEvents() {
  const teacherQuery = document.getElementById("teacher-query");
  const studentQuery = document.getElementById("student-query");
  const runSearch = async input => {
    adminQuery = input.value.trim();
    adminPage = 1;
    teacherSelection.clear();
    await renderAdmin();
  };
  const teacherSearch = document.getElementById("teacher-search");
  if (teacherSearch) teacherSearch.addEventListener("click", () => runSearch(teacherQuery));
  if (teacherQuery) teacherQuery.addEventListener("keydown", event => {
    if (event.key === "Enter") runSearch(teacherQuery);
  });
  const studentSearch = document.getElementById("student-search");
  if (studentSearch) studentSearch.addEventListener("click", () => runSearch(studentQuery));
  if (studentQuery) studentQuery.addEventListener("keydown", event => {
    if (event.key === "Enter") runSearch(studentQuery);
  });
  const auditQuery = document.getElementById("audit-query");
  const auditSearch = document.getElementById("audit-search");
  if (auditSearch) auditSearch.addEventListener("click", () => runSearch(auditQuery));
  if (auditQuery) auditQuery.addEventListener("keydown", event => {
    if (event.key === "Enter") runSearch(auditQuery);
  });
  const prev = document.querySelector(".admin-prev");
  if (prev) prev.addEventListener("click", async () => {
    teacherSelection.clear();
    adminPage -= 1;
    await renderAdmin();
  });
  const next = document.querySelector(".admin-next");
  if (next) next.addEventListener("click", async () => {
    teacherSelection.clear();
    adminPage += 1;
    await renderAdmin();
  });
  const newTeacher = document.getElementById("bt-new-teacher");
  if (newTeacher) newTeacher.addEventListener("click", () => userModal("teacher"));
  const importTeachers = document.getElementById("bt-import-teachers");
  if (importTeachers) importTeachers.addEventListener("click", teacherImportModal);
  const deleteTeachers = document.getElementById("bt-delete-teachers");
  if (deleteTeachers) deleteTeachers.addEventListener("click", batchDeleteTeachers);
  const newStudent = document.getElementById("bt-new-student");
  if (newStudent) newStudent.addEventListener("click", () => userModal("student"));
  const importBtn = document.getElementById("bt-import-students");
  if (importBtn) importBtn.addEventListener("click", studentImportModal);
  const newClass = document.getElementById("bt-new-class");
  if (newClass) newClass.addEventListener("click", () => classModal());
  bindUserRowEvents();
  bindClassEvents();
  bindModuleEvents();
  bindAiEvents();
  bindSchoolEvents();
}

function bindSchoolEvents() {
  const rollover = document.getElementById("academic-rollover");
  if (rollover) rollover.addEventListener("click", openAcademicRolloverPreview);
  const backup = document.getElementById("backup-create");
  if (backup) backup.addEventListener("click", async () => {
    backup.disabled = true;
    try {
      await api("/api/admin/recovery/backup", { method: "POST" });
      toast("数据库备份已创建", "ok");
      await renderAdmin();
    } catch (error) { toast(error.message, "error"); backup.disabled = false; }
  });
  document.querySelectorAll(".backup-preview").forEach(button => button.addEventListener("click", () =>
    openBackupPreview(button.dataset.id)));
  const saveSettings = document.getElementById("school-settings-save");
  if (saveSettings) saveSettings.addEventListener("click", async () => {
    saveSettings.disabled = true;
    try {
      await api("/api/admin/school-settings", { method: "PUT", body: JSON.stringify({
        schoolName: document.getElementById("school-name").value.trim(),
        academicYearNote: document.getElementById("school-year-note").value.trim(),
        dailyTokenLimit: Number(document.getElementById("daily-token-limit").value || 0),
        monthlyTokenLimit: Number(document.getElementById("monthly-token-limit").value || 0),
      }) });
      toast("学校设置已保存", "ok");
      await renderAdmin();
    } catch (error) { toast(error.message, "error"); saveSettings.disabled = false; }
  });
}

async function openAcademicRolloverPreview() {
  const active = adminAcademicYears.find(item => item.status === "active");
  if (!active) return;
  try {
    const preview = await api("/api/admin/academic-years/rollover/preview", {
      method: "POST", body: JSON.stringify({ targetStartYear: Number(active.startYear) + 1 }),
    });
    const blockers = preview.blockers || {};
    openModal(`
      <div class="modal-title">${h(preview.current.label)} → ${h(preview.target.label)}</div>
      ${preview.canConfirm ? '<div class="banner-key plain">结转会自动备份；旧作业和旧班级冻结，新班级不继承任课老师。</div>'
        : `<div class="banner-key plain danger-text">暂不能结转：活动任务 ${blockers.activeJobs || 0}，草稿 ${blockers.draftAssignments || 0}，待发布结果 ${blockers.pendingResults || 0}。</div>`}
      <div class="table-wrap rollover-table"><table class="table responsive-table"><thead><tr><th>原班级</th><th>人数</th><th>新年级</th><th>新班号</th><th>新班级名称</th></tr></thead>
        <tbody>${preview.classes.map((item, index) => `<tr class="rollover-row" data-index="${index}" data-source="${h(item.sourceClassId)}">
          <td data-label="原班级"><b>${h(item.sourceName)}</b>${item.graduating ? '<small class="table-subtext">六年级默认毕业</small>' : ""}</td>
          <td data-label="人数">${item.studentCount}</td>
          ${item.graduating ? '<td colspan="3" data-label="去向"><span class="chip gray">毕业并停用账号</span></td>' : `
            <td data-label="新年级"><input class="input compact rollover-grade" value="${h(item.targetGrade)}"></td>
            <td data-label="新班号"><input class="input compact rollover-klass" value="${h(item.targetKlass)}"></td>
            <td data-label="新班级名称"><input class="input compact rollover-name" value="${h(item.targetName)}"></td>`}
        </tr>`).join("")}</tbody></table></div>
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal(true)">取消</button>
        <button class="btn danger" id="rollover-confirm" ${preview.canConfirm ? "" : "disabled"}>备份并结转</button></div>`, { dismissible: false });
    document.getElementById("rollover-confirm").addEventListener("click", async event => {
      if (!confirm("确认冻结当前学年并建立新班级？该操作只能通过备份恢复撤销。")) return;
      const classes = preview.classes.filter(item => !item.graduating).map(item => {
        const index = preview.classes.indexOf(item);
        const row = document.querySelector(`.rollover-row[data-index="${index}"]`);
        return {
          sourceClassId: item.sourceClassId,
          targetGrade: row.querySelector(".rollover-grade").value.trim(),
          targetKlass: row.querySelector(".rollover-klass").value.trim(),
          targetName: row.querySelector(".rollover-name").value.trim(),
          studentUsernames: item.studentUsernames,
        };
      });
      event.currentTarget.disabled = true;
      try {
        const result = await api("/api/admin/academic-years/rollover/confirm", {
          method: "POST", body: JSON.stringify({ targetStartYear: preview.target.startYear, classes }),
        });
        closeModal(true);
        toast(`已建立 ${result.createdClasses.length} 个新班级，毕业 ${result.graduated} 人`, "ok", 5000);
        await renderAdmin();
      } catch (error) { toast(error.message, "error", 5200); event.currentTarget.disabled = false; }
    });
  } catch (error) { toast(error.message, "error"); }
}

async function openBackupPreview(backupId) {
  try {
    const preview = await api("/api/admin/recovery/preview", {
      method: "POST", body: JSON.stringify({ backupId }),
    });
    openModal(`
      <div class="modal-title">备份预检</div>
      <div class="banner-key plain">完整性检查通过，数据库版本 ${preview.schemaVersion}。</div>
      <div class="usage-summary">${Object.entries(preview.counts || {}).map(([key, value]) => `<div><span>${h(key)}</span><b>${value}</b></div>`).join("")}</div>
      <div class="form-note">确认后会生成待恢复标记；关闭并重新启动服务时才会原子替换数据库，当前数据库会先再次备份。</div>
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button>
        <button class="btn danger" id="restore-schedule">安排恢复</button></div>`);
    document.getElementById("restore-schedule").addEventListener("click", async event => {
      if (!confirm("确定安排恢复这个备份？重启服务后生效。")) return;
      event.currentTarget.disabled = true;
      try {
        await api("/api/admin/recovery/schedule", {
          method: "POST", body: JSON.stringify({ backupId }),
        });
        closeModal();
        toast("恢复已安排，请关闭并重新启动服务", "ok", 6000);
      } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
    });
  } catch (error) { toast(error.message, "error"); }
}

function bindModuleEvents() {
  const refresh = document.getElementById("module-refresh");
  if (refresh) refresh.addEventListener("click", renderAdmin);
  document.querySelectorAll(".module-state").forEach(button => button.addEventListener("click", async () => {
    const module = adminModules.find(item => item.key === button.dataset.key);
    const disabling = button.dataset.state === "disabled";
    const verb = disabling ? "停用" : "启用";
    if (disabling && !confirm(`确定停用“${module ? module.name : button.dataset.key}”？入口会隐藏，历史数据不会删除。`)) return;
    button.disabled = true;
    try {
      const result = await api(`/api/admin/modules/${encodeURIComponent(button.dataset.key)}/state`, {
        method: "POST", body: JSON.stringify({ state: button.dataset.state }),
      });
      const featureData = await api("/api/features");
      window.features = featureData.features || {};
      toast(result.module.status === "draining" ? "模块正在等待现有任务结束" : `模块已${verb}`, "ok");
      await renderAdmin();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  }));
}

function bindAiEvents() {
  const connection = document.getElementById("ai-connection");
  if (connection) connection.addEventListener("click", openSettings);
  const test = document.getElementById("ai-test");
  if (test) test.addEventListener("click", async () => {
    test.disabled = true;
    test.textContent = "测试中…";
    const results = [];
    for (const profile of ["article_judge", "ecnu_plus"]) {
      try {
        const r = await api(`/api/admin/ai/profiles/${profile}/test`, {
          method: "POST", body: JSON.stringify({}),
        });
        results.push(`${profile === "article_judge" ? "作文业务" : "OCR 识别"}：${r.model} 正常`);
      } catch (e) {
        results.push(`${profile === "article_judge" ? "作文业务" : "OCR 识别"}：${e.message}`);
      }
    }
    const failed = results.some(r => !r.endsWith("正常"));
    toast(results.join("；"), failed ? "error" : "ok", 6000);
    test.disabled = false;
    test.textContent = "测试 AI 配置";
  });
  const save = document.getElementById("ai-routes-save");
  if (save) save.addEventListener("click", async () => {
    const routes = [...document.querySelectorAll(".ai-route-row")].map(row => ({
      taskType: row.dataset.task,
      model: row.querySelector(".ai-route-model").value.trim(),
      jsonMode: row.querySelector(".ai-route-json").checked,
      thinking: row.querySelector(".ai-route-thinking").checked,
      maxTokens: Number(row.querySelector(".ai-route-tokens").value),
    }));
    save.disabled = true;
    try {
      await api("/api/admin/ai/routes", {
        method: "PUT", body: JSON.stringify({ routes }),
      });
      toast("AI 任务路由已保存", "ok");
      await renderAdmin();
    } catch (error) {
      toast(error.message, "error");
      save.disabled = false;
    }
  });
}

function bindUserRowEvents() {
  const selectAll = document.getElementById("teacher-select-all");
  if (selectAll) {
    const selectable = adminUsers.map(user => user.username);
    selectAll.checked = selectable.length > 0 && selectable.every(username => teacherSelection.has(username));
    selectAll.addEventListener("change", () => {
      selectable.forEach(username => selectAll.checked ? teacherSelection.add(username) : teacherSelection.delete(username));
      renderAdmin();
    });
  }
  document.querySelectorAll(".teacher-select").forEach(input => input.addEventListener("change", () => {
    if (input.checked) teacherSelection.add(input.dataset.username);
    else teacherSelection.delete(input.dataset.username);
    const button = document.getElementById("bt-delete-teachers");
    if (button) button.disabled = !teacherSelection.size;
  }));
  document.querySelectorAll(".user-edit").forEach(b => b.addEventListener("click", () => {
    const user = adminUsers.find(u => u.username === b.dataset.username);
    if (user) userModal(user.role, user);
  }));
  document.querySelectorAll(".user-reset").forEach(button => button.addEventListener("click", async () => {
    const user = adminUsers.find(item => item.username === button.dataset.username);
    if (!confirm(`确定重置 ${user ? user.display_name : button.dataset.username} 的密码？该账号现有会话会立即失效。`)) return;
    try {
      const result = await api(`/api/admin/users/${encodeURIComponent(button.dataset.username)}/reset-password`, {
        method: "POST", body: JSON.stringify({}),
      });
      showTemporaryPassword(button.dataset.username, result.temporaryPassword);
    } catch (error) { toast(error.message, "error"); }
  }));
  document.querySelectorAll(".user-del").forEach(b => b.addEventListener("click", async () => {
    const user = adminUsers.find(u => u.username === b.dataset.username);
    if (!confirm(`确定删除 ${user ? user.display_name : b.dataset.username}？`)) return;
    try {
      const result = await api(`/api/admin/users/${encodeURIComponent(b.dataset.username)}`, { method: "DELETE" });
      const message = (result.deactivated || []).length ? "老师已有业务记录，账号已安全停用" : "老师账号已删除";
      toast(message, "ok");
      await renderAdmin();
    } catch (e) { toast(e.message, "error"); }
  }));
  document.querySelectorAll(".user-enable").forEach(button => button.addEventListener("click", async () => {
    const user = adminUsers.find(item => item.username === button.dataset.username);
    if (!user) return;
    try {
      await api(`/api/admin/users/${encodeURIComponent(user.username)}`, {
        method: "PUT",
        body: JSON.stringify({
          employeeNo: user.employeeNo || user.username,
          display_name: user.display_name,
          subject: user.subject,
          classIds: (user.classes || []).map(item => item.id),
          active: true,
        }),
      });
      toast("老师账号已重新启用", "ok");
      await renderAdmin();
    } catch (error) { toast(error.message, "error"); }
  }));
  document.querySelectorAll(".student-status").forEach(button => button.addEventListener("click", () =>
    changeStudentStatus(button.dataset.username, button.dataset.status)));
  document.querySelectorAll(".teacher-transfer").forEach(button => button.addEventListener("click", () =>
    openTeacherTransfer(button.dataset.username)));
}

async function changeStudentStatus(username, status) {
  const user = adminUsers.find(item => item.username === username);
  if (status !== "active") {
    const label = status === "graduated" ? "毕业" : "转学";
    if (!confirm(`确认将 ${user?.display_name || username} 标记为${label}？账号会停用并撤销会话，历史记录保留。`)) return;
    try {
      await api(`/api/admin/students/${encodeURIComponent(username)}/status`, {
        method: "POST", body: JSON.stringify({ status }),
      });
      toast(`学生已标记为${label}`, "ok");
      await renderAdmin();
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  openModal(`
    <div class="modal-title">恢复学生在校状态</div>
    <label class="field"><span>当前班级</span><select class="input" id="restore-student-class">${adminClasses.map(item =>
      `<option value="${h(item.id)}">${h(item.name)}</option>`).join("")}</select></label>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="restore-student-confirm">恢复</button></div>`);
  document.getElementById("restore-student-confirm").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      await api(`/api/admin/students/${encodeURIComponent(username)}/status`, {
        method: "POST", body: JSON.stringify({ status: "active", classId: document.getElementById("restore-student-class").value }),
      });
      closeModal(); toast("学生已恢复并加入当前班级", "ok"); await renderAdmin();
    } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
  });
}

async function openTeacherTransfer(username) {
  try {
    const response = await api("/api/admin/users?role=teacher&page=1&pageSize=100");
    const targets = (response.users || []).filter(item => item.active && item.username !== username);
    if (!targets.length) { toast("没有可接收作业的在职老师", "error"); return; }
    openModal(`
      <div class="modal-title">移交老师作业</div>
      <div class="banner-key plain">移交全部历史和当前作业的管理权，学生记录及原老师快照保持不变。</div>
      <label class="field"><span>接收老师</span><select class="input" id="transfer-target">${targets.map(item =>
        `<option value="${h(item.username)}">${h(item.display_name)} / ${h(item.employeeNo || item.username)}</option>`).join("")}</select></label>
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
        <button class="btn danger" id="transfer-confirm">移交全部作业</button></div>`);
    document.getElementById("transfer-confirm").addEventListener("click", async event => {
      if (!confirm("确认移交该老师的全部作业？")) return;
      event.currentTarget.disabled = true;
      try {
        const result = await api(`/api/admin/teachers/${encodeURIComponent(username)}/transfer-assignments`, {
          method: "POST", body: JSON.stringify({ targetUsername: document.getElementById("transfer-target").value }),
        });
        closeModal(); toast(`已移交 ${result.assignmentIds.length} 项作业`, "ok");
      } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
    });
  } catch (error) { toast(error.message, "error"); }
}

async function batchDeleteTeachers() {
  const usernames = [...teacherSelection];
  if (!usernames.length) return;
  if (!confirm(`确定处理选中的 ${usernames.length} 位老师？有作业记录的老师会停用，其他老师会删除。`)) return;
  try {
    const result = await api("/api/admin/teachers/batch-delete", {
      method: "POST", body: JSON.stringify({ usernames }),
    });
    teacherSelection.clear();
    toast(`处理完成：删除 ${result.deleted.length}，停用 ${result.deactivated.length}，失败 ${result.failed.length}`, result.failed.length ? "info" : "ok", 5200);
    await renderAdmin();
  } catch (error) { toast(error.message, "error"); }
}

function bindClassEvents() {
  document.querySelectorAll(".class-edit").forEach(b => b.addEventListener("click", () => {
    classModal(adminClasses.find(c => c.id === b.dataset.id));
  }));
  document.querySelectorAll(".class-del").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确定删除该班级？已有作业的班级不能删除。")) return;
    try {
      await api(`/api/classes/${b.dataset.id}`, { method: "DELETE" });
      toast("班级已删除", "ok");
      await renderAdmin();
    } catch (e) { toast(e.message, "error"); }
  }));
  document.querySelectorAll(".add-teacher").forEach(btn => btn.addEventListener("click", () => memberPickerModal(btn.dataset.id, "teacher")));
  document.querySelectorAll(".add-student").forEach(btn => btn.addEventListener("click", () => memberPickerModal(btn.dataset.id, "student")));
  document.querySelectorAll(".view-members").forEach(btn => btn.addEventListener("click", () => {
    classMembersModal(btn.dataset.id, btn.dataset.role);
  }));
  document.querySelectorAll(".remove-teacher").forEach(b => b.addEventListener("click", async () => {
    await api(`/api/classes/${b.dataset.cid}/teachers/${encodeURIComponent(b.dataset.username)}`, { method: "DELETE" });
    toast("已移出老师", "ok");
    await renderAdmin();
  }));
  document.querySelectorAll(".remove-student").forEach(b => b.addEventListener("click", async () => {
    await api(`/api/classes/${b.dataset.cid}/members/${encodeURIComponent(b.dataset.username)}`, { method: "DELETE" });
    toast("已移出学生", "ok");
    await renderAdmin();
  }));
}

async function classMembersModal(classId, role, page = 1, query = "") {
  const cls = adminClasses.find(c => c.id === classId);
  if (!cls) return;
  const roleText = role === "teacher" ? "老师" : "学生";
  const params = new URLSearchParams({ role, q: query, page: String(page), pageSize: "20" });
  const response = await api(`/api/classes/${classId}/members?${params}`);
  const members = response.members || [];
  const pages = Math.max(1, Math.ceil((response.total || 0) / (response.pageSize || 20)));
  openModal(`
    <div class="modal-title">${h(cls.name)} · ${roleText}成员</div>
    <div class="data-toolbar">
      <div class="search-group"><input class="input" id="class-member-query" value="${h(query)}" placeholder="搜索姓名、用户名${role === "student" ? "或学号" : ""}">
        <button class="btn" id="class-member-search">搜索</button></div>
      <button class="btn primary" id="class-member-add">加入${roleText}</button>
    </div>
    <div class="table-wrap"><table class="table responsive-table"><thead><tr><th>姓名</th><th>${role === "student" ? "学号" : "用户名"}</th><th>操作</th></tr></thead>
      <tbody>${members.map(member => `<tr><td data-label="姓名"><b>${h(member.display_name)}</b></td><td data-label="${role === "student" ? "学号" : "用户名"}">${h(role === "student" ? member.studentNo || member.username : member.username)}</td>
        <td data-label="操作"><button class="btn small danger member-remove" data-username="${h(member.username)}">移出</button></td></tr>`).join("") || '<tr><td colspan="3" class="empty compact-empty">暂无成员</td></tr>'}</tbody></table></div>
    <div class="modal-actions">
      <button class="btn small ghost" id="class-member-prev" ${page <= 1 ? "disabled" : ""}>上一页</button>
      <span>第 ${page} / ${pages} 页，共 ${response.total || 0} 人</span>
      <button class="btn small ghost" id="class-member-next" ${page >= pages ? "disabled" : ""}>下一页</button>
      <button class="btn ghost" onclick="closeModal()">关闭</button>
    </div>`);
  const search = () => classMembersModal(classId, role, 1, document.getElementById("class-member-query").value.trim());
  document.getElementById("class-member-search").addEventListener("click", search);
  document.getElementById("class-member-query").addEventListener("keydown", event => { if (event.key === "Enter") search(); });
  document.getElementById("class-member-add").addEventListener("click", () => memberPickerModal(classId, role));
  document.getElementById("class-member-prev").addEventListener("click", () => classMembersModal(classId, role, page - 1, query));
  document.getElementById("class-member-next").addEventListener("click", () => classMembersModal(classId, role, page + 1, query));
  document.querySelectorAll(".member-remove").forEach(button => button.addEventListener("click", async () => {
    const endpoint = role === "teacher" ? "teachers" : "members";
    await api(`/api/classes/${classId}/${endpoint}/${encodeURIComponent(button.dataset.username)}`, { method: "DELETE" });
    toast(`已移出${roleText}`, "ok");
    await loadAdminData();
    await classMembersModal(classId, role, page, query);
  }));
}

async function memberPickerModal(classId, role) {
  const cls = adminClasses.find(c => c.id === classId);
  if (!cls) return;
  const selected = new Set();
  const roleText = role === "teacher" ? "老师" : "学生";
  const endpoint = role === "teacher" ? "teachers" : "members";
  openModal(`
    <div class="modal-title">加入${roleText}到 ${h(cls.name)}</div>
    <label class="field"><span>搜索姓名、用户名${role === "student" ? "或学号" : ""}</span>
      <input class="input" id="member-picker-query" placeholder="输入关键词后从结果中勾选"></label>
    <div class="member-picker-list" id="member-picker-list"><div class="empty compact-empty">正在加载...</div></div>
    <div class="modal-actions">
      <span class="chip blue" style="margin-right:auto">已选择 <b id="member-picker-count">0</b> 个</span>
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="member-picker-save">批量加入</button>
    </div>`);
  const renderList = async query => {
    const params = new URLSearchParams({ role, q: query, page: "1", pageSize: "50" });
    const response = await api(`/api/admin/users?${params}`);
    const box = document.getElementById("member-picker-list");
    if (!box) return;
    box.innerHTML = (response.users || []).map(user => {
      const inClass = (user.classes || []).some(item => item.id === classId);
      const detail = role === "student" ? studentLabel(user) : `${user.display_name} / ${user.username}`;
      return `<label class="member-choice ${inClass ? "disabled" : ""}">
        <input type="checkbox" value="${h(user.username)}" ${selected.has(user.username) ? "checked" : ""} ${inClass ? "disabled" : ""}>
        <span><b>${h(user.display_name)}</b><em>${h(detail)}${inClass ? " · 已在班级" : ""}</em></span></label>`;
    }).join("") || '<div class="empty compact-empty">没有匹配账号</div>';
    box.querySelectorAll("input[type=checkbox]").forEach(input => input.addEventListener("change", () => {
      if (input.checked) selected.add(input.value); else selected.delete(input.value);
      document.getElementById("member-picker-count").textContent = selected.size;
    }));
  };
  let searchTimer = null;
  const queryInput = document.getElementById("member-picker-query");
  queryInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderList(queryInput.value.trim()), 250);
  });
  document.getElementById("member-picker-save").addEventListener("click", async event => {
    if (!selected.size) { toast(`请选择要加入的${roleText}`, "error"); return; }
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "加入中...";
    try {
      for (const username of selected) {
        await api(`/api/classes/${classId}/${endpoint}`, { method: "POST", body: JSON.stringify({ username }) });
      }
      toast(`已加入 ${selected.size} 个${roleText}`, "ok");
      closeModal();
      await renderAdmin();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = "批量加入";
    }
  });
  await renderList("");
}

function userModal(role, user = null, draft = null) {
  const isStudent = role === "student";
  const teacherDraft = !isStudent ? (draft || {
    employeeNo: user ? (user.employeeNo || user.username) : "",
    display_name: user ? user.display_name : "",
    subject: user ? (user.subject || "") : "",
    classIds: user ? (user.classes || []).map(item => item.id) : [],
    password: "",
    active: user ? user.active : true,
  }) : null;
  const selectedClassNames = !isStudent ? sortedClasses(teacherDraft.classIds.map(id => {
    const klass = adminClasses.find(item => item.id === id);
    return klass || { id, name: id, grade: "", klass: "" };
  })).map(item => item.name) : [];
  openModal(`
    <div class="modal-title">${user ? "编辑" : "新建"}${isStudent ? "学生" : "老师"}</div>
    ${isStudent ? `
      <label class="field"><span>学号 <span class="req">*</span></span>
        <input class="input" id="uf-student-no" value="${h(user ? (user.studentNo || user.username) : "")}" placeholder="学生唯一学号"></label>` : ""}
    ${isStudent ? `<label class="field"><span>用户名（留空则使用学号）</span>
      <input class="input" id="uf-username" value="${h(user ? user.username : "")}" ${user ? "disabled" : ""} placeholder="默认等于学号"></label>` : `
      <label class="field"><span>工号 <span class="req">*</span></span>
        <input class="input" id="uf-employee-no" value="${h(teacherDraft.employeeNo)}" placeholder="工号同时作为登录账号"></label>`}
    <label class="field"><span>姓名 <span class="req">*</span></span>
      <input class="input" id="uf-name" value="${h(isStudent ? (user ? user.display_name : "") : teacherDraft.display_name)}" placeholder="显示姓名"></label>
    ${isStudent ? `<div class="picker-context">权限：${permissionName(role)}。班级归属通过班级管理分配。</div>` : `
      <label class="field"><span>学科 <span class="req">*</span></span>
        <input class="input" id="uf-subject" value="${h(teacherDraft.subject)}" placeholder="如：语文"></label>
      <div class="field"><span>任教班级 <span class="req">*</span></span>
        <div class="class-picker-summary">${selectedClassNames.map(name => `<span class="chip blue">${h(name)}</span>`).join(" ") || "尚未选择班级"}</div>
        <button class="btn" id="uf-pick-classes" type="button">搜索选择班级</button>
      </div>`}
    ${user ? "" : `<label class="field"><span>初始密码</span>
      <input class="input" id="uf-password" type="password" minlength="10" value="${h(teacherDraft ? teacherDraft.password : "")}" placeholder="留空则生成临时密码"></label>
      <div class="form-note">自定义密码至少 10 位；新账号首次登录仍必须修改密码。</div>`}
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="uf-save">保存</button>
    </div>`);
  if (!isStudent) document.getElementById("uf-pick-classes").addEventListener("click", () => {
    teacherDraft.employeeNo = document.getElementById("uf-employee-no").value.trim();
    teacherDraft.display_name = document.getElementById("uf-name").value.trim();
    teacherDraft.subject = document.getElementById("uf-subject").value.trim();
    if (!user) teacherDraft.password = document.getElementById("uf-password").value;
    teacherClassPickerModal(user, teacherDraft);
  });
  document.getElementById("uf-save").addEventListener("click", async () => {
    const studentNo = isStudent ? document.getElementById("uf-student-no").value.trim() : "";
    const body = {
      username: isStudent ? (document.getElementById("uf-username").value.trim() || studentNo)
        : document.getElementById("uf-employee-no").value.trim(),
      display_name: document.getElementById("uf-name").value.trim(),
      role,
    };
    if (!user) body.password = document.getElementById("uf-password").value;
    if (isStudent) body.studentNo = studentNo;
    else {
      body.employeeNo = document.getElementById("uf-employee-no").value.trim();
      body.subject = document.getElementById("uf-subject").value.trim();
      body.classIds = teacherDraft.classIds;
      body.active = teacherDraft.active;
    }
    try {
      let result;
      if (user) result = await api(`/api/admin/users/${encodeURIComponent(user.username)}`, { method: "PUT", body: JSON.stringify(body) });
      else result = await api("/api/admin/users", { method: "POST", body: JSON.stringify(body) });
      toast("账号已保存", "ok");
      await renderAdmin();
      if (result.temporaryPassword) showTemporaryPassword(result.username || body.username, result.temporaryPassword);
      else closeModal();
    } catch (e) { toast(e.message, "error"); }
  });
}

function teacherClassPickerModal(user, draft) {
  const selected = new Set(draft.classIds || []);
  const renderList = query => {
    const key = (query || "").trim().toLowerCase();
    const visible = sortedClasses(adminClasses.filter(item => !key || `${item.name} ${item.grade} ${item.klass}`.toLowerCase().includes(key)));
    document.getElementById("teacher-class-list").innerHTML = visible.map(item => `
      <label class="picker-option"><input type="checkbox" value="${h(item.id)}" ${selected.has(item.id) ? "checked" : ""}>
        <span><b>${h(item.name)}</b><small>${h(item.grade || "")} ${h(item.klass || "")}</small></span></label>`).join("")
      || '<div class="empty compact-empty">没有匹配班级</div>';
    document.querySelectorAll("#teacher-class-list input").forEach(input => input.addEventListener("change", () => {
      if (input.checked) selected.add(input.value); else selected.delete(input.value);
    }));
  };
  openModal(`
    <div class="modal-title">选择任教班级</div>
    <input class="input" id="teacher-class-query" placeholder="搜索班级名称或年级">
    <div id="teacher-class-list" class="picker-list"></div>
    <div class="modal-actions"><button class="btn ghost" id="teacher-class-back">返回</button>
      <button class="btn primary" id="teacher-class-save">确认选择</button></div>`);
  const query = document.getElementById("teacher-class-query");
  query.addEventListener("input", () => renderList(query.value));
  document.getElementById("teacher-class-back").addEventListener("click", () => userModal("teacher", user, draft));
  document.getElementById("teacher-class-save").addEventListener("click", () => {
    draft.classIds = sortedClasses(adminClasses.filter(item => selected.has(item.id))).map(item => item.id);
    userModal("teacher", user, draft);
  });
  renderList("");
}

function showTemporaryPassword(username, password) {
  openModal(`
    <div class="modal-title">临时密码已生成</div>
    <div class="picker-context">请将临时密码安全告知 <b>${h(username)}</b>。该账号首次登录后必须修改密码。</div>
    <label class="field"><span>临时密码</span>
      <input class="input" id="temporary-password" value="${h(password || "")}" readonly></label>
    <div class="modal-actions">
      <button class="btn ghost" id="temporary-copy">复制</button>
      <button class="btn primary" onclick="closeModal()">完成</button>
    </div>`);
  document.getElementById("temporary-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(password || "");
      toast("临时密码已复制", "ok");
    } catch (error) {
      document.getElementById("temporary-password").select();
    }
  });
}

function teacherImportModal() {
  openModal(`
    <div class="modal-title">老师 Excel 导入</div>
    <div class="picker-context">表头使用“姓名、工号、学科、班级”。多个班级可用顿号、逗号、分号或换行分隔；班级必须已由管理员建立。</div>
    <label class="field"><span>选择 .xlsx 文件</span>
      <input class="input" id="teacher-import-file" type="file" accept=".xlsx"></label>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="teacher-import-preview">预览导入</button></div>`);
  document.getElementById("teacher-import-preview").addEventListener("click", previewTeacherImport);
}

async function previewTeacherImport() {
  const file = document.getElementById("teacher-import-file").files[0];
  if (!file) { toast("请选择 Excel 文件", "error"); return; }
  const button = document.getElementById("teacher-import-preview");
  button.disabled = true;
  button.textContent = "解析中...";
  const form = new FormData();
  form.append("file", file);
  try {
    const preview = await api("/api/admin/teachers/import/preview", { method: "POST", body: form });
    renderTeacherImportPreview(preview);
  } catch (error) {
    toast(error.message, "error", 5200);
    button.disabled = false;
    button.textContent = "预览导入";
  }
}

function renderTeacherImportPreview(preview, requestedPage = 1) {
  const view = teacherImportPreviewView(preview, requestedPage);
  const { summary, rows, page, pageCount } = view;
  document.getElementById("modal-box").innerHTML = view.html;
  document.getElementById("teacher-import-prev")?.addEventListener("click", () => renderTeacherImportPreview(preview, page - 1));
  document.getElementById("teacher-import-next")?.addEventListener("click", () => renderTeacherImportPreview(preview, page + 1));
  document.getElementById("teacher-import-cancel").addEventListener("click", () => closeModal());
  document.getElementById("teacher-import-confirm")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = `正在导入 ${summary.total || rows.length} 位老师...`;
    setModalDismissible(false);
    try {
      const result = await api("/api/admin/teachers/import/confirm", {
        method: "POST", body: JSON.stringify({ token: preview.token }),
      });
      setModalDismissible(true);
      toast(`导入完成：新增 ${result.created}，更新 ${result.updated}`, "ok", 5200);
      await renderAdmin();
      if (result.defaultTemporaryPassword) showTemporaryPassword("本次新增老师", result.defaultTemporaryPassword);
      else closeModal();
    } catch (error) {
      setModalDismissible(true);
      button.disabled = false;
      button.textContent = "确认导入";
      toast(error.message, "error", 5200);
    }
  });
}

function studentImportModal() {
  openModal(`
    <div class="modal-title">学生 Excel 导入</div>
    <div class="picker-context">
      可只提供“班级、姓名”，系统能识别“三(01)”“三(1)班”“三年级1班”。已有学号会保留；缺少学号时按入学年份、年级、班级和班内顺序生成。
    </div>
    <label class="field"><span>当前学年起始年份</span>
      <input class="input" id="import-academic-year" type="number" min="2000" max="${new Date().getFullYear() + 1}" value="${defaultAcademicYearStart()}">
      <small>例如 2025 学年中，三年级 1 班第 1 名学生生成 2023030101。</small>
    </label>
    <label class="field"><span>选择 .xlsx 文件</span>
      <input class="input" id="import-file" type="file" accept=".xlsx"></label>
    <div id="import-preview"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="import-preview-btn">预览并审核</button>
    </div>`);
  document.getElementById("import-preview-btn").addEventListener("click", previewStudentImport);
}

async function previewStudentImport() {
  const file = document.getElementById("import-file").files[0];
  if (!file) { toast("请选择 Excel 文件", "error"); return; }
  const academicYearStart = document.getElementById("import-academic-year").value.trim();
  if (!/^\d{4}$/.test(academicYearStart)) { toast("请输入 4 位当前学年起始年份", "error"); return; }
  const btn = document.getElementById("import-preview-btn");
  btn.disabled = true;
  btn.textContent = "审核中...";
  const fd = new FormData();
  fd.append("file", file);
  fd.append("academicYearStart", academicYearStart);
  try {
    const preview = await api("/api/admin/students/import/preview", { method: "POST", body: fd });
    renderImportPreview(preview);
  } catch (e) {
    toast(e.message, "error", 5200);
    btn.disabled = false;
    btn.textContent = "预览并审核";
  }
}

function renderImportPreview(preview, requestedPage = 1) {
  const view = studentImportPreviewView(preview, requestedPage);
  const { summary: s, rows, page, pageCount } = view;
  document.getElementById("modal-box").innerHTML = view.html;
  document.getElementById("import-page-prev")?.addEventListener("click", () => {
    renderImportPreview(preview, page - 1);
  });
  document.getElementById("import-page-next")?.addEventListener("click", () => {
    renderImportPreview(preview, page + 1);
  });
  const btn = document.getElementById("import-confirm-btn");
  if (btn) btn.addEventListener("click", async () => {
    const cancelBtn = document.getElementById("import-cancel-btn");
    btn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    setModalDismissible(false);
    btn.textContent = `正在导入 ${s.total || rows.length} 人...`;
    try {
      const r = await api("/api/admin/students/import/confirm", {
        method: "POST",
        body: JSON.stringify({ token: preview.token }),
      });
      setModalDismissible(true);
      const duration = Number.isFinite(Number(r.durationSeconds)) ? `，耗时 ${Number(r.durationSeconds).toFixed(1)} 秒` : "";
      toast(`导入完成：新增 ${r.created}，更新 ${r.updated}${duration}`, "ok", 5200);
      await renderAdmin();
      if (r.defaultTemporaryPassword) showTemporaryPassword("本次新增学生", r.defaultTemporaryPassword);
      else closeModal();
    } catch (e) {
      toast(e.message, "error", 5200);
      setModalDismissible(true);
      if (cancelBtn) cancelBtn.disabled = false;
      btn.disabled = false;
      btn.textContent = "确认导入";
    }
  });
}

function classModal(cls = null) {
  openModal(`
    <div class="modal-title">${cls ? "编辑班级" : "＋ 新建班级"}</div>
    <label class="field"><span>班级名称</span>
      <input class="input" id="cf-name" value="${h(cls ? cls.name : "")}" placeholder="如：五年级3班"></label>
    <div style="display:flex;gap:10px">
      <label class="field" style="flex:1"><span>年级</span>
        <input class="input" id="cf-grade" value="${h(cls ? cls.grade : "")}" placeholder="如：五年级"></label>
      <label class="field" style="flex:1"><span>班级</span>
        <input class="input" id="cf-klass" value="${h(cls ? cls.klass : "")}" placeholder="如：3班"></label>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="cf-save">保存</button>
    </div>`);
  document.getElementById("cf-save").addEventListener("click", async () => {
    const body = {
      name: document.getElementById("cf-name").value.trim(),
      grade: document.getElementById("cf-grade").value.trim(),
      klass: document.getElementById("cf-klass").value.trim(),
    };
    try {
      if (cls) await api(`/api/classes/${cls.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/classes", { method: "POST", body: JSON.stringify(body) });
      toast("班级已保存", "ok");
      closeModal();
      await renderAdmin();
    } catch (e) { toast(e.message, "error"); }
  });
}
