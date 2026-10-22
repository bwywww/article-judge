/* ============ 老师页面逻辑 ============ */
import {
  api, applyFeatureVisibility, closeModal, dateTimeLocalToUtc,
  downloadPdf, fmtScore, formatLocalDateTime, h, openModal, openPasswordChangeModal, pageInit,
  renderFeedback, setModalDismissible, toast, toDateTimeLocalValue, wait,
} from "./shared/index.js";
import { deadlineText, lifecycleChip, lifecycleText } from "./teacher/assignments.js";
import {
  buildStudentTrendChartConfig, renderAnalyticsPanel, renderInsightEditForm,
} from "./teacher/analytics.js";
import {
  candidateStudentLabel, candidateVisible, duplicateCandidateUsernames,
  isPlaceholderName, matchCandidateStudent,
} from "./teacher/imports.js";
import { renderGradingProgress, renderResultsTable, STATUS_CHIP } from "./teacher/reporting.js";
import { defaultFeedbackGrade, feedbackListText } from "./teacher/review.js";
import { renderReturnSubmissionForm, renderRevisionTimeline } from "./teacher/revisions.js";

let assignments = [];
let classes = [];
let cur = null;          // 当前打开的作业详情
let candidates = null;   // 解析出的待确认名单
let candidatesUploadToken = "";  // 待确认原件令牌，确认名单后才保存为正式原件
let candidateStudents = [];
let candidateFilter = "all";
let importJob = null;
let importPollGeneration = 0;
let detailTab = "overview";
let curRanking = [];
let curInsight = null;
let curShowcase = [];
let teacherTrendChart = null;
let assignmentQuery = "";
let pollTimer = null;
let pollStartTs = 0;     // 轮询开始时间（用于估算剩余时间）
let pollFinishedBase = 0;

pageInit(["teacher"], async () => {
  document.getElementById("btn-new-assignment").addEventListener("click", newAssignmentModal);
  document.getElementById("btn-records").addEventListener("click", showRecords);
  document.getElementById("btn-dashboard").addEventListener("click", showDashboard);
  document.getElementById("btn-students").addEventListener("click", showStudents);
  document.getElementById("btn-assignments").addEventListener("click", showAssignmentManagement);
  document.getElementById("btn-profile").addEventListener("click", showProfile);
  document.getElementById("assignment-query").addEventListener("input", event => {
    assignmentQuery = event.target.value.trim().toLowerCase();
    renderAssignmentList();
  });
  // #file-input 是模板里的持久节点，change 只绑定一次，避免 renderDetail 重复绑造成事件泄漏。
  document.getElementById("file-input").addEventListener("change", () => {
    const input = document.getElementById("file-input");
    if (input.files.length) onUploadFile(input.files[0]);
  });
  await loadClasses();
  await loadList();
  await showDashboard();
});

async function loadClasses() {
  const r = await api("/api/classes");
  classes = r.classes || [];
}

/* ---------- 学生作业提交记录 ---------- */
let recAssignments = [];
let recFilter = "";
let recQuery = "";
let recPage = 1;

async function showRecords() {
  stopPolling();
  recAssignments = (await api("/api/assignments")).assignments;
  showTeacherSection("records");
  await renderRecords();
}

function hideRecords() {
  showAssignmentManagement();
}

const SECTION_IDS = ["dashboard", "assignment-management", "detail", "records", "students", "profile"];
const NAV_IDS = ["dashboard", "students", "assignments", "records", "profile"];

function showTeacherSection(section, nav = section) {
  SECTION_IDS.forEach(id => {
    const element = document.getElementById(id);
    if (element) element.hidden = id !== section;
  });
  NAV_IDS.forEach(id => document.getElementById(`btn-${id}`)?.classList.toggle("active", id === nav));
  document.body.classList.remove("nav-open");
}

function latestAssignedItem() {
  const candidates = assignments.filter(item => item.lifecycleStatus !== "draft" && item.lifecycleStatus !== "archived");
  return (candidates.length ? candidates : assignments)[0] || null;
}

async function showDashboard() {
  stopPolling();
  showTeacherSection("dashboard");
  const box = document.getElementById("dashboard");
  const latest = latestAssignedItem();
  if (!latest) {
    box.innerHTML = `<div class="empty workspace-empty"><h2>还没有布置作文</h2><p>请到“作文管理”中布置第一篇作文。</p>
      <button class="btn primary" id="dashboard-manage">进入作文管理</button></div>`;
    document.getElementById("dashboard-manage").addEventListener("click", showAssignmentManagement);
    return;
  }
  try {
    const item = await api(`/api/assignments/${latest.id}`);
    const stats = item.stats || {};
    box.innerHTML = `
      <div class="page-heading compact-heading"><div><h1>最近作文</h1><p>最近一次布置的学生作文概况。</p></div>
        <button class="btn primary" id="dashboard-open">管理本次作文</button></div>
      <section class="recent-assignment-card">
        <div class="recent-assignment-head"><div><span class="chip blue">${h(item.className || "未分班")}</span>
          <h2>${h(item.title)}</h2><p>${h(item.prompt || "未填写写作要求")}</p></div>${lifecycleChip(item)}</div>
        <div class="stats-row dashboard-stats">
          <div class="stat-pill"><div class="v">${stats.total ?? 0}</div><div class="k">已提交</div></div>
          <div class="stat-pill"><div class="v">${stats.missing ?? 0}</div><div class="k">未提交</div></div>
          <div class="stat-pill"><div class="v">${stats.done ?? 0}</div><div class="k">已批改</div></div>
          <div class="stat-pill"><div class="v">${stats.pendingReview ?? 0}</div><div class="k">待发布</div></div>
          <div class="stat-pill"><div class="v">${stats.avg ?? "—"}</div><div class="k">平均分</div></div>
        </div>
        <div class="recent-meta">布置时间 ${h(item.publishedAt || item.createdAt || "—")} · ${h(deadlineText(item))}</div>
      </section>`;
    document.getElementById("dashboard-open").addEventListener("click", () => openAssignment(item.id));
  } catch (error) {
    box.innerHTML = `<div class="error-panel"><h2>最近作文加载失败</h2><p>${h(error.message)}</p>
      <button class="btn" id="dashboard-retry">重试</button></div>`;
    document.getElementById("dashboard-retry").addEventListener("click", showDashboard);
  }
}

function showAssignmentManagement() {
  stopPolling();
  showTeacherSection("assignment-management", "assignments");
  renderAssignmentList();
}

async function showStudents() {
  stopPolling();
  showTeacherSection("students");
  const box = document.getElementById("students");
  try {
    const result = await api("/api/students");
    const students = result.students || [];
    box.innerHTML = `<div class="page-heading compact-heading"><div><h1>学生信息</h1><p>仅显示当前任教班级的在校学生。</p></div></div>
      <div class="data-surface"><div class="data-toolbar"><div class="search-group">
        <input class="input" id="teacher-student-query" placeholder="搜索姓名、学号或班级"></div><span class="data-count">共 ${students.length} 人</span></div>
        <div id="teacher-student-list"></div></div>`;
    const render = query => {
      const key = (query || "").trim().toLowerCase();
      const visible = students.filter(student => !key || `${student.display_name} ${student.studentNo} ${(student.classes || []).map(item => item.name).join(" ")}`.toLowerCase().includes(key));
      document.getElementById("teacher-student-list").innerHTML = visible.length ? `<div class="table-wrap"><table class="table responsive-table">
        <thead><tr><th>姓名</th><th>学号</th><th>班级</th><th>状态</th><th>操作</th></tr></thead><tbody>${visible.map(student => `<tr>
          <td data-label="姓名"><b>${h(student.display_name)}</b></td><td data-label="学号">${h(student.studentNo || student.username)}</td>
          <td data-label="班级">${h((student.classes || []).map(item => item.name).join("、") || "—")}</td>
          <td data-label="状态">${student.active ? "在校" : "停用"}</td><td data-label="操作"><button class="btn small ghost student-info-trend" data-username="${h(student.username)}" data-name="${h(student.display_name)}">成长趋势</button></td>
        </tr>`).join("")}</tbody></table></div>` : '<div class="empty compact-empty">没有匹配学生</div>';
      document.querySelectorAll(".student-info-trend").forEach(button => button.addEventListener("click", () =>
        showStudentTrendModal(button.dataset.username)));
    };
    const query = document.getElementById("teacher-student-query");
    query.addEventListener("input", () => render(query.value));
    render("");
  } catch (error) {
    box.innerHTML = `<div class="error-panel"><h2>学生信息加载失败</h2><p>${h(error.message)}</p><button class="btn" id="students-retry">重试</button></div>`;
    document.getElementById("students-retry").addEventListener("click", showStudents);
  }
}

async function showProfile() {
  stopPolling();
  showTeacherSection("profile");
  const box = document.getElementById("profile");
  try {
    const me = await api("/api/me");
    box.innerHTML = `<div class="page-heading compact-heading"><div><h1>我的信息</h1><p>账号资料由管理员维护。</p></div>
      <button class="btn" id="profile-password">修改密码</button></div>
      <section class="profile-card"><dl>
        <div><dt>姓名</dt><dd>${h(me.display_name || me.username)}</dd></div>
        <div><dt>工号</dt><dd>${h(me.employeeNo || me.username)}</dd></div>
        <div><dt>学科</dt><dd>${h(me.subject || "未填写")}</dd></div>
        <div><dt>任教班级</dt><dd>${h(classes.map(item => item.name).join("、") || "未分配")}</dd></div>
      </dl></section>`;
    document.getElementById("profile-password").addEventListener("click", () => openPasswordChangeModal({ forced: false }));
  } catch (error) {
    box.innerHTML = `<div class="error-panel"><h2>个人信息加载失败</h2><p>${h(error.message)}</p><button class="btn" id="profile-retry">重试</button></div>`;
    document.getElementById("profile-retry").addEventListener("click", showProfile);
  }
}

async function renderRecords() {
  const query = new URLSearchParams({ q: recQuery, page: String(recPage), pageSize: "30" });
  if (recFilter) query.set("assignmentId", recFilter);
  const r = await api(`/api/teacher/records?${query}`);
  const box = document.getElementById("records");
  box.hidden = false;
  box.innerHTML = `
    <div class="detail-head">
      <div><h1>学生作业提交记录</h1>
        <div style="color:var(--text-2);font-size:13px;margin-top:4px">仅显示你发布作业下的学生提交，不包含学生自主练习</div></div>
      <div class="ops">
        <button class="btn small ghost" id="btn-clone">复制作业</button>
        <select class="input" id="rec-filter" style="width:200px">
          <option value="">全部作业</option>
          ${recAssignments.map(a => `<option value="${h(a.id)}" ${recFilter === a.id ? "selected" : ""}>${h(a.title)}</option>`).join("")}
        </select>
        <button class="btn ghost" id="bt-back-records">返回作业</button>
      </div>
    </div>
    <div class="data-surface">
      <div class="data-toolbar"><div class="search-group"><input class="input" id="rec-query" value="${h(recQuery)}" placeholder="搜索学生、作文或作业">
        <button class="btn" id="rec-search">搜索</button></div><span class="data-count">共 ${r.total || 0} 条</span></div>
      ${r.records.length ? `<div class="table-wrap"><table class="table responsive-table">
        <thead><tr><th>学生</th><th>作业</th><th>作文题目</th><th>分数</th><th>等级</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>${r.records.map(rec => `
          <tr>
            <td data-label="学生"><b>${h(rec.displayName)}</b></td>
            <td data-label="作业">${h(rec.assignmentTitle || "未关联作业")}</td>
            <td data-label="作文题目" style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h(rec.title || "（无标题）")}</td>
            <td data-label="分数">${fmtScore(rec.score)}</td>
            <td data-label="等级">${h(rec.level || "—")}</td>
            <td data-label="批改时间" style="color:var(--text-2);font-size:12.5px">${h(rec.gradedAt)}</td>
            <td data-label="操作">
              <button class="btn small ghost rec-view" data-id="${rec.id}">详情</button>
              <button class="btn small ghost rec-export" data-id="${rec.id}">导出 PDF</button>
            </td>
          </tr>`).join("")}</tbody>
      </table></div>` : `<div class="empty compact-empty"><p>还没有学生作业提交记录</p></div>`}
      <div class="pagination"><button class="btn small ghost" id="rec-prev" ${recPage <= 1 ? "disabled" : ""}>上一页</button>
        <span>第 ${recPage} / ${Math.max(1, Math.ceil((r.total || 0) / 30))} 页</span>
        <button class="btn small ghost" id="rec-next" ${recPage * 30 >= (r.total || 0) ? "disabled" : ""}>下一页</button></div>
    </div>`;
  document.getElementById("bt-back-records").addEventListener("click", hideRecords);
  document.getElementById("btn-clone").addEventListener("click", () => cloneFromRecords());
  const sel = document.getElementById("rec-filter");
  sel.addEventListener("change", () => { recFilter = sel.value; recPage = 1; renderRecords(); });
  const searchRecords = () => {
    recQuery = document.getElementById("rec-query").value.trim();
    recPage = 1;
    renderRecords();
  };
  document.getElementById("rec-search").addEventListener("click", searchRecords);
  document.getElementById("rec-query").addEventListener("keydown", event => { if (event.key === "Enter") searchRecords(); });
  document.getElementById("rec-prev").addEventListener("click", () => { recPage -= 1; renderRecords(); });
  document.getElementById("rec-next").addEventListener("click", () => { recPage += 1; renderRecords(); });
  box.querySelectorAll(".rec-view").forEach(b => b.addEventListener("click", () => showRecordDetail(b.dataset.id, r.records)));
  box.querySelectorAll(".rec-export").forEach(b => b.addEventListener("click", async () => {
    try { await downloadPdf(`/api/teacher/records/${b.dataset.id}/export`); toast("已下载该生批改意见", "ok"); }
    catch (e) { toast(e.message, "error"); }
  }));
}

async function cloneFromRecords() {
  if (!recFilter) { toast("请先在筛选下拉框中选择要复制的作业", "info"); return; }
  try {
    const detail = await api(`/api/assignments/${recFilter}`);
    const fallback = classes.some(item => item.id === detail.classId) ? detail.classId : (classes[0]?.id || "");
    openModal(`
      <div class="modal-title">复制作业为新草稿</div>
      <label class="field"><span>新作业名称</span><input class="input" id="clone-title" value="${h(detail.title)}（副本）"></label>
      <label class="field"><span>目标班级</span><select class="input" id="clone-class">${classes.map(item =>
        `<option value="${h(item.id)}" ${item.id === fallback ? "selected" : ""}>${h(item.name)}</option>`).join("")}</select></label>
      <div class="form-note">仅复制题目、要求、评分标准和字数，不复制学生、作文和截止时间。</div>
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
        <button class="btn primary" id="clone-confirm">创建草稿</button></div>`);
    document.getElementById("clone-confirm").addEventListener("click", async event => {
      event.currentTarget.disabled = true;
      try {
        const result = await api(`/api/assignments/${detail.id}/clone`, {
          method: "POST", body: JSON.stringify({
            title: document.getElementById("clone-title").value.trim(),
            classId: document.getElementById("clone-class").value,
          }),
        });
        closeModal();
        toast("新草稿已创建", "ok");
        await loadList();
        await openAssignment(result.id);
      } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
    });
  } catch (error) { toast(error.message, "error"); }
}

function showRecordDetail(id, records) {
  const rec = records.find(x => x.id === id);
  if (!rec) return;
  openModal(`
    <div class="modal-title">${h(rec.displayName)}　${fmtScore(rec.score)}　${h(rec.level || "")}
      <span class="chip gray">${h(rec.assignmentTitle || "未关联作业")}</span></div>
    <div style="color:var(--text-2);font-size:13px;margin-bottom:10px">
      作文题目：${h(rec.title || "（无标题）")}　·　批改时间：${h(rec.gradedAt)}</div>
    <div id="fb-box"></div>
    <details class="essay-text"><summary>查看作文原文</summary>
      <div class="essay-preview">${h(rec.text)}</div></details>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button></div>`);
  renderFeedback(document.getElementById("fb-box"), { ...rec.result, gradedAt: rec.gradedAt });
}

/* ---------- 作业列表 ---------- */
async function loadList() {
  const r = await api("/api/assignments");
  assignments = [...(r.assignments || [])].sort((a, b) =>
    String(b.publishedAt || b.createdAt || "").localeCompare(String(a.publishedAt || a.createdAt || "")));
  renderAssignmentList();
}

function renderAssignmentList() {
  const ul = document.getElementById("assignment-list");
  const visible = assignments.filter(a => !assignmentQuery || `${a.title || ""} ${a.className || ""}`.toLowerCase().includes(assignmentQuery));
  const count = document.getElementById("assignment-count");
  if (count) count.textContent = `共 ${visible.length} 次作文`;
  ul.innerHTML = visible.map(a => `
    <button type="button" data-id="${h(a.id)}" class="assignment-card ${cur && cur.id === a.id ? "active" : ""}">
      <span class="assignment-card-top"><span class="chip gray">${h(a.className || "未分班")}</span>${lifecycleChip(a)}</span>
      <strong>${h(a.title)}</strong>
      <span class="assignment-card-meta">布置于 ${h(a.publishedAt || a.createdAt || "—")}</span>
      <span class="assignment-card-stats"><b>${a.total}</b> 篇 · <b>${a.done}</b> 已批改${a.pendingReview ? ` · <b>${a.pendingReview}</b> 待发布` : ""}${a.avg != null ? ` · 均分 <b>${a.avg}</b>` : ""}</span>
      <span class="assignment-card-action">进入本次作文管理 →</span>
    </button>`).join("") || '<div class="empty compact-empty"><h2>还没有作文任务</h2><p>点击右上角“布置作文”创建草稿。</p></div>';
  ul.querySelectorAll("button[data-id]").forEach(li => {
    li.addEventListener("click", () => {
      document.body.classList.remove("nav-open");
      const menu = document.querySelector(".workspace-menu");
      if (menu) menu.setAttribute("aria-expanded", "false");
      openAssignment(li.dataset.id);
    });
  });
}

/* ---------- 新建 / 编辑作业 ---------- */
function assignmentFormHtml(a) {
  const v = k => a ? (a[k] || "") : "";
  const rememberedClass = localStorage.getItem("teacher:lastClassId") || "";
  const selectedClass = v("classId") || (classes.some(item => item.id === rememberedClass) ? rememberedClass : "") || (classes[0] && classes[0].id) || "";
  return `
    <div class="modal-title">${a ? "编辑作业" : "＋ 新建作业"}</div>
    <label class="field"><span>作文主题 / 任务名称<span class="req"> *</span></span>
      <input class="input" id="af-title" value="${h(v("title"))}" placeholder="如：家乡的变化（学生可自拟标题）"></label>
    <label class="field"><span>所属班级</span>
      <select class="input" id="af-class">
        ${classes.length ? classes.map(c => `<option value="${h(c.id)}" ${selectedClass === c.id ? "selected" : ""}>${h(c.name)}</option>`).join("")
                         : '<option value="">默认班级</option>'}
      </select></label>
    <label class="field"><span>写作要求</span>
      <textarea class="input" id="af-prompt" rows="4" placeholder="说明文体、写作要点，以及标题是否自拟">${h(v("prompt"))}</textarea></label>
    <label class="field"><span>评分标准</span>
      <textarea class="input" id="af-criteria" rows="3" placeholder="如：立意 30 分、结构 30 分、语言 30 分、书写 10 分；也可留空由 AI 自主把握">${h(v("criteria"))}</textarea></label>
    <label class="field"><span>字数要求</span>
      <input class="input" id="af-word" value="${h(v("wordLimit"))}" placeholder="如：不少于 600 字"></label>
    <label class="field"><span>截止时间</span>
      <input class="input" id="af-deadline" type="datetime-local" value="${h(toDateTimeLocalValue(v("deadlineAt")))}"></label>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="af-save">${a ? "保存修改" : "创建作业"}</button>
    </div>`;
}

async function newAssignmentModal() {
  await loadClasses();
  openModal(assignmentFormHtml(null));
  document.getElementById("af-save").addEventListener("click", async () => {
    const body = {
      title: document.getElementById("af-title").value.trim(),
      classId: document.getElementById("af-class").value,
      prompt: document.getElementById("af-prompt").value.trim(),
      criteria: document.getElementById("af-criteria").value.trim(),
      wordLimit: document.getElementById("af-word").value.trim(),
      deadlineAt: dateTimeLocalToUtc(document.getElementById("af-deadline").value),
    };
    try {
      localStorage.setItem("teacher:lastClassId", body.classId);
      const r = await api("/api/assignments", { method: "POST", body: JSON.stringify(body) });
      toast("作业草稿已创建", "ok");
      closeModal();
      await loadList();
      await openAssignment(r.id);
    } catch (e) { toast(e.message, "error"); }
  });
}

function editAssignmentModal() {
  openModal(assignmentFormHtml(cur));
  document.getElementById("af-save").addEventListener("click", async () => {
    try {
      await api("/api/assignments/" + cur.id, { method: "PUT", body: JSON.stringify({
        title: document.getElementById("af-title").value.trim(),
        classId: document.getElementById("af-class").value,
        prompt: document.getElementById("af-prompt").value.trim(),
        criteria: document.getElementById("af-criteria").value.trim(),
        wordLimit: document.getElementById("af-word").value.trim(),
        deadlineAt: dateTimeLocalToUtc(document.getElementById("af-deadline").value),
      }) });
      toast("已保存", "ok");
      closeModal();
      await openAssignment(cur.id);
      await loadList();
    } catch (e) { toast(e.message, "error"); }
  });
}

async function deleteAssignment() {
  if (!confirm(`确定删除空草稿《${cur.title}》？`)) return;
  await api("/api/assignments/" + cur.id, { method: "DELETE" });
  toast("作业已删除", "ok");
  cur = null; candidates = null;
  document.getElementById("detail").hidden = true;
  await loadList();
  showAssignmentManagement();
}

async function transitionAssignment(action, body = {}) {
  try {
    await api(`/api/assignments/${cur.id}/${action}`, {
      method: "POST", body: JSON.stringify(body),
    });
    const messages = {
      publish: "作业已发布", unpublish: "作业已撤回为草稿",
      close: "作业已关闭", reopen: "作业已重新开放", archive: "作业已归档",
    };
    toast(messages[action] || "作业状态已更新", "ok");
    await openAssignment(cur.id);
  } catch (error) { toast(error.message, "error", 5000); }
}

function reopenAssignmentModal() {
  const current = toDateTimeLocalValue(cur.deadlineAt);
  openModal(`
    <div class="modal-title">重新开放作业</div>
    <label class="field"><span>新的截止时间${cur.closedReason === "deadline" ? " *" : ""}</span>
      <input class="input" id="reopen-deadline" type="datetime-local" value="${h(current)}"></label>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="reopen-save">重新开放</button>
    </div>`);
  document.getElementById("reopen-save").addEventListener("click", async () => {
    try {
      const value = document.getElementById("reopen-deadline").value;
      await transitionAssignment("reopen", { deadlineAt: dateTimeLocalToUtc(value) });
      closeModal();
    } catch (error) { toast(error.message, "error"); }
  });
}

/* ---------- 打开作业详情 ---------- */
async function openAssignment(id) {
  stopPolling();
  importPollGeneration += 1;
  showTeacherSection("detail", "assignments");
  // 刷新当前作业时保留当前 tab；打开其他作业才重算（有批改中→results，否则 overview）。
  const keepTab = id === cur?.id;
  cur = await api("/api/assignments/" + id);
  await loadAssignmentExtras();
  candidates = null;
  candidatesUploadToken = "";
  candidateStudents = [];
  importJob = null;
  await restoreImportJob(id);
  if (!keepTab) {
    detailTab = (cur.essays || []).some(e => ["queued", "grading"].includes(e.status)) ? "results" : "overview";
  }
  renderDetail();
  await loadList();
  // 刷新页面时若批改仍在进行，自动恢复轮询，避免进度"冻住"
  if ((cur.essays || []).some(e => ["queued", "grading"].includes(e.status))) {
    startPolling();
  }
}

async function loadAssignmentExtras() {
  const tasks = [];
  if (window.features?.analytics?.available) {
    tasks.push(api(`/api/assignments/${cur.id}/analytics/ranking`)
      .then(result => { curRanking = result.ranking || []; })
      .catch(() => { curRanking = []; }));
    tasks.push(api(`/api/assignments/${cur.id}/analytics/insight`)
      .then(result => { curInsight = result.insight || null; })
      .catch(() => { curInsight = null; }));
  } else {
    curRanking = [];
    curInsight = null;
  }
  if (window.features?.showcase?.available) {
    tasks.push(api(`/api/assignments/${cur.id}/showcase`)
      .then(result => { curShowcase = result.items || []; })
      .catch(() => { curShowcase = []; }));
  } else curShowcase = [];
  await Promise.all(tasks);
}

function renderDetail() {
  const d = document.getElementById("detail");
  d.hidden = false;
  const s = cur.stats || {};
  d.innerHTML = `
    <div class="detail-head">
      <div>
        <button class="btn small ghost detail-back" id="btn-detail-back">← 返回作文管理</button>
        <h1>${h(cur.title)} ${lifecycleChip(cur)}</h1>
        <div style="color:var(--text-2);font-size:13px;margin-top:4px">创建于 ${h(cur.createdAt || "")} · ${h(deadlineText(cur))}</div>
      </div>
      <div class="ops">
        <button class="btn small ghost" id="btn-clone">复制为新草稿</button>
        ${cur.hasSource ? '<button class="btn small" id="btn-source">原始文件</button>' : ""}
        ${cur.lifecycleStatus === "draft" ? '<button class="btn small primary" id="btn-publish">发布作业</button>' : ""}
        ${cur.lifecycleStatus === "published" ? '<button class="btn small" id="btn-close-assignment">关闭作业</button>' : ""}
        ${cur.lifecycleStatus === "published" && !(cur.essays || []).length ? '<button class="btn small ghost" id="btn-unpublish">撤回草稿</button>' : ""}
        ${cur.lifecycleStatus === "closed" ? '<button class="btn small primary" id="btn-reopen-assignment">重新开放</button><button class="btn small" id="btn-archive">归档</button>' : ""}
        ${!["closed", "archived"].includes(cur.lifecycleStatus) && !(cur.essays || []).length ? '<button class="btn small" id="btn-edit">编辑作业</button>' : ""}
        ${cur.lifecycleStatus === "draft" && (cur.essays || []).length ? '<button class="btn small danger" id="btn-clear">清空作文</button>' : ""}
        ${cur.canDelete ? '<button class="btn small danger" id="btn-del">删除草稿</button>' : ""}
      </div>
    </div>
    <div class="detail-tabs" role="tablist">
      <button class="${detailTab === "overview" ? "active" : ""}" data-detail-tab="overview">概览</button>
      <button class="${detailTab === "import" ? "active" : ""}" data-detail-tab="import">名单与导入</button>
      <button class="${detailTab === "results" ? "active" : ""}" data-detail-tab="results">批改结果</button>
      ${window.features?.analytics?.available ? `<button class="${detailTab === "analytics" ? "active" : ""}" data-detail-tab="analytics">班级分析</button>` : ""}
    </div>
    <div class="detail-panel">${detailTab === "overview" ? renderOverviewPanel(s)
      : detailTab === "import" ? renderImportPanel()
      : detailTab === "analytics" ? renderAnalyticsPanel(cur, curInsight, curShowcase) : renderResultPanel()}</div>`;

  const bind = (id, handler) => {
    const element = document.getElementById(id);
    if (element) element.addEventListener("click", handler);
  };
  bind("btn-detail-back", showAssignmentManagement);
  bind("btn-edit", editAssignmentModal);
  bind("btn-clone", cloneAssignmentModal);
  bind("btn-del", deleteAssignment);
  bind("btn-publish", () => transitionAssignment("publish"));
  bind("btn-unpublish", () => transitionAssignment("unpublish"));
  bind("btn-close-assignment", () => {
    if (confirm("关闭后学生将不能继续提交，确定关闭？")) transitionAssignment("close");
  });
  bind("btn-reopen-assignment", reopenAssignmentModal);
  bind("btn-archive", () => {
    if (confirm("归档后学生端不再显示该作业，记录仍会保留。确定归档？")) transitionAssignment("archive");
  });
  bind("btn-clear", async () => {
    if (!confirm("确定清空该作业的全部作文、学生提交、老师导入锁定和批改结果？学生端提交状态也会被清除。")) return;
    await api(`/api/assignments/${cur.id}/clear`, { method: "POST" });
    cur.essays = []; candidates = null; candidateStudents = [];
    detailTab = "import";
    renderDetail(); await loadList();
  });
  bind("btn-import-cancel", cancelImportJob);
  bind("btn-import-retry", retryImportJob);
  // #dz 随 innerHTML 每次重建，这里只绑 dz 交互；#file-input 的 change 已在 pageInit 绑一次。
  const dz = document.getElementById("dz");
  const fileInput = document.getElementById("file-input");
  if (dz && fileInput) {
    dz.addEventListener("click", () => fileInput.click());
    dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
    dz.addEventListener("drop", e => {
      e.preventDefault(); dz.classList.remove("drag");
      if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; onUploadFile(fileInput.files[0]); }
    });
  }
}

function renderOverviewPanel(s) {
  return `
    <section class="overview-panel">
      <div class="stats-row">
        <div class="stat-pill"><div class="v">${s.total ?? 0}</div><div class="k">已提交</div></div>
        <div class="stat-pill"><div class="v">${s.missing ?? 0}</div><div class="k">未提交</div></div>
        <div class="stat-pill"><div class="v" style="color:var(--success)">${s.done ?? 0}</div><div class="k">已批改</div></div>
        <div class="stat-pill"><div class="v" style="color:var(--warning)">${s.pendingReview ?? 0}</div><div class="k">待发布</div></div>
        <div class="stat-pill"><div class="v" style="color:var(--danger)">${s.error ?? 0}</div><div class="k">失败</div></div>
        <div class="stat-pill"><div class="v">${s.avg != null ? s.avg : "—"}</div><div class="k">平均分</div></div>
      </div>
      <div class="meta-grid">
        ${cur.prompt ? `<div class="item full"><b>题目与要求：</b>${h(cur.prompt)}</div>` : ""}
        ${cur.criteria ? `<div class="item full"><b>评分标准：</b>${h(cur.criteria)}</div>` : ""}
        ${cur.wordLimit ? `<div class="item"><b>字数要求：</b>${h(cur.wordLimit)}</div>` : ""}
        ${cur.className ? `<div class="item"><b>所属班级：</b>${h(cur.className)}</div>` : ""}
        <div class="item"><b>作业状态：</b>${h(lifecycleText(cur.lifecycleStatus))}</div>
        <div class="item"><b>截止时间：</b>${h(cur.deadlineAt ? formatLocalDateTime(cur.deadlineAt) : "无")}</div>
      </div>
      ${(cur.missingStudents || []).length ? `<div class="missing-roster">
        <div class="section-title"><h3>未交名单</h3><span class="chip gray">${cur.missingStudents.length} 人</span></div>
        <div class="roster-inline">${cur.missingStudents.map(student => `<span>${h(student.display_name)} / ${h(student.studentNo || student.username)}</span>`).join("")}</div>
      </div>` : ""}
    </section>`;
}

function renderImportPanel() {
  if (candidates) return renderCandidateConfirm();
  const essays = cur.essays || [];
  return `${cur.lifecycleStatus === "archived" ? '<div class="banner-key plain">作业已归档，名单仅供查看。</div>' : importJob ? renderImportProgress() : renderUploadZone()}
    ${essays.length ? `<section class="data-surface roster-summary">
      <div class="pane-head"><h2>当前名单</h2><span class="data-count">${essays.length} 篇</span></div>
      <div class="table-wrap"><table class="table responsive-table"><thead><tr><th>学生</th><th>作文标题</th><th>学号/账号</th><th>提交方式</th><th>状态</th></tr></thead>
        <tbody>${essays.map(essay => `<tr><td data-label="学生"><b>${h(essay.studentName)}</b></td><td data-label="作文标题">${h(essay.studentTitle || cur.title)}</td><td data-label="学号 / 账号">${h(essay.submittedBy || "未关联")}</td>
          <td data-label="提交方式">${essay.submissionType === "student_upload" ? "学生提交" : "老师导入"}</td><td data-label="状态">${STATUS_CHIP[essay.status] || ""}</td></tr>`).join("")}</tbody>
      </table></div></section>` : ""}`;
}

function renderResultPanel() {
  const essays = cur.essays || [];
  if (!essays.length) return '<div class="empty compact-empty"><h2>还没有作文</h2><p>请先到“名单与导入”上传或等待学生提交。</p></div>';
  const active = essays.filter(e => ["queued", "grading"].includes(e.status));
  if (active.length) return renderGradingProgress(essays, pollFinishedBase, pollStartTs);
  return renderResultsTable(essays, curRanking);
}

function renderUploadZone() {
  const ocrState = window.features?.ocr?.status || "enabled";
  const ocrOff = ocrState !== "enabled";
  return `
    <div class="section-title"><h3>上传学生作文</h3><span class="chip gray">自动识别文字版 / 扫描件</span></div>
    <div class="import-options">
      <label class="field"><span>作文纸拆分方式</span>
        <select class="input" id="import-split-mode">
          <option value="auto">自动识别姓名栏与跨页（推荐）</option>
          <option value="one_page">专用作文纸：每页一篇</option>
          <option value="two_pages">专用作文纸：每两页一篇（正反面）</option>
        </select></label>
      <div class="form-note">最多 60 MB / 150 页。扫描件逐页 OCR；保存前可修改姓名、标题、顺序及合并拆分。</div>
    </div>
    <div class="dropzone" id="dz">
      <div class="dz-icon">PDF / DOCX / TXT / PNG / JPG</div>
      <div style="font-size:15px;font-weight:600">点击或拖入全班作文文件（建议每篇分页，40-50 名学生）</div>
      <div style="margin-top:6px;font-size:13px">${ocrOff ? `智能导入当前为 ${h(ocrState)}，不能创建新任务` : "文字页直接提取；扫描页 / 图片逐页识别，刷新后可恢复进度"}</div>
    </div>`;
}

function renderImportProgress() {
  const total = importJob.totalPages || 0;
  const completed = importJob.completedPages || 0;
  const percent = total ? Math.round(completed * 100 / total) : 0;
  const active = ["queued", "extracting", "ocr"].includes(importJob.status);
  return `<section class="data-surface" style="padding:20px">
    <div class="section-title"><h3>智能导入任务</h3><span class="chip ${importJob.status === "error" ? "red" : "orange"}">${h(importJob.stage || importJob.status)}</span></div>
    <p>${active ? `正在逐页处理：${completed} / ${total || "待计算"} 页` : importJob.status === "error" ? h(importJob.errorAdvice || importJob.error || "识别失败") : "任务已取消"}</p>
    ${total ? `<div class="progress"><div class="progress-bar" style="width:${percent}%"></div></div>` : ""}
    <div style="margin-top:12px;display:flex;gap:8px">
      ${active ? '<button class="btn danger" id="btn-import-cancel">取消任务</button>' : ""}
      ${importJob.status === "error" ? '<button class="btn primary" id="btn-import-retry">重试失败任务</button>' : ""}
    </div>
  </section>`;
}

function onUploadFile(file) {
  if (!file) return;
  if (!cur) return;
  uploadFile(file);
}

async function uploadFile(file) {
  if (cur && cur.essays.length && !confirm("上传新文件将合并导入；同一学生的旧提交可能被老师导入覆盖并锁定，确定继续？")) {
    document.getElementById("file-input").value = "";
    return;
  }
  if (window.features?.ocr?.status && window.features.ocr.status !== "enabled") {
    toast("智能导入当前不可创建新任务，请联系管理员", "error", 6000);
    document.getElementById("file-input").value = "";
    return;
  }
  const fd = new FormData();
  fd.append("file", file);
  fd.append("splitMode", document.getElementById("import-split-mode")?.value || "auto");
  toast(`已上传 ${file.name}，正在创建识别任务…`);
  try {
    const assignmentId = cur.id;
    const r = await api(`/api/assignments/${assignmentId}/import-jobs`, { method: "POST", body: fd });
    importJob = { ...r, assignmentId };
    localStorage.setItem(`ocr-job:${assignmentId}`, r.jobId);
    detailTab = "import";
    renderDetail();
    pollImportJob(assignmentId, r.jobId, ++importPollGeneration);
  } catch (e) {
    toast(e.message, "error", 5000);
  }
  document.getElementById("file-input").value = "";
}

async function restoreImportJob(assignmentId) {
  const jobId = localStorage.getItem(`ocr-job:${assignmentId}`);
  if (!jobId) return;
  try {
    const job = await api(`/api/assignments/${assignmentId}/import-jobs/${jobId}`);
    importJob = { ...job, assignmentId };
    if (job.status === "done") applyImportResult(job);
    else if (["queued", "extracting", "ocr"].includes(job.status)) {
      pollImportJob(assignmentId, jobId, ++importPollGeneration);
    } else if (job.status === "cancelled") {
      localStorage.removeItem(`ocr-job:${assignmentId}`);
      importJob = null;
    }
  } catch (error) {
    localStorage.removeItem(`ocr-job:${assignmentId}`);
    importJob = null;
  }
}

function applyImportResult(job) {
  candidates = job.essays || [];
  candidatesUploadToken = job.uploadToken || "";
  candidateStudents = job.students || [];
  candidateFilter = "all";
  importJob = null;
}

async function pollImportJob(assignmentId, jobId, generation) {
  let delay = 1000;
  while (generation === importPollGeneration) {
    try {
      const job = await api(`/api/assignments/${assignmentId}/import-jobs/${jobId}`);
      if (generation !== importPollGeneration) return;
      importJob = { ...job, assignmentId };
      if (job.status === "done") {
        applyImportResult(job);
        if (cur?.id === assignmentId) renderDetail();
        toast(`识别出 ${(job.essays || []).length} 篇作文，请核对姓名`, "ok");
        return;
      }
      if (["error", "cancelled"].includes(job.status)) {
        if (job.status === "cancelled") localStorage.removeItem(`ocr-job:${assignmentId}`);
        if (cur?.id === assignmentId) renderDetail();
        if (job.status === "error") toast(job.errorAdvice || job.error || "识别失败", "error", 6000);
        return;
      }
      if (cur?.id === assignmentId) renderDetail();
      await wait(delay);
      delay = Math.min(5000, Math.round(delay * 1.5));
    } catch (error) {
      if (cur?.id === assignmentId) toast(`进度查询失败：${error.message}`, "error");
      await wait(Math.min(5000, delay));
      delay = Math.min(5000, Math.round(delay * 1.5));
    }
  }
}

async function cancelImportJob() {
  if (!importJob || !cur) return;
  try {
    await api(`/api/assignments/${cur.id}/import-jobs/${importJob.jobId}/cancel`, { method: "POST" });
    toast("已请求取消，当前页结束后会清理临时文件", "ok");
  } catch (error) { toast(error.message, "error"); }
}

async function retryImportJob() {
  if (!importJob || !cur) return;
  try {
    const assignmentId = cur.id;
    const job = await api(`/api/assignments/${assignmentId}/import-jobs/${importJob.jobId}/retry`, { method: "POST" });
    importJob = { ...job, assignmentId };
    renderDetail();
    pollImportJob(assignmentId, job.jobId, ++importPollGeneration);
  } catch (error) { toast(error.message, "error"); }
}

function syncCandidateRows() {
  document.querySelectorAll(".candidate-row").forEach(r => {
    const i = +r.dataset.i;
    if (!candidates[i]) return;
    const name = r.querySelector(".name");
    if (name) candidates[i].studentName = name.value.trim();
    const title = r.querySelector(".essay-title");
    if (title) candidates[i].studentTitle = title.value.trim();
  });
}

function renderCandidateConfirm() {
  const dupes = duplicateCandidateUsernames(candidates || []);
  const visible = candidates.map((c, i) => ({ c, i }))
    .filter(x => candidateVisible(x.c, dupes, candidateFilter));
  const rows = visible.map(({ c, i }) => {
    const boundLabel = candidateStudentLabel(c.submittedBy, candidateStudents);
    const conflict = c.submittedBy && dupes.has(c.submittedBy);
    const canEditStructure = candidateFilter === "all";
    return `
    <div class="candidate-row" data-i="${i}">
      <span class="idx">${i + 1}</span>
      <input class="input name" value="${h(c.studentName)}" placeholder="学生姓名">
      <input class="input essay-title" value="${h(c.studentTitle || "")}" maxlength="120" placeholder="作文标题（可修改）">
      <span class="bind-status ${c.submittedBy ? "ok" : ""} ${conflict ? "warn" : ""}">
        ${conflict ? "重复关联：" : c.submittedBy ? "已关联：" : "未关联账号"}
        ${c.submittedBy ? h(boundLabel || c.submittedBy) : ""}
        ${!c.submittedBy && c.studentNoHint ? ` · 原稿学号 ${h(c.studentNoHint)}` : ""}
      </span>
      ${isPlaceholderName(c.studentName) ? '<span class="chip orange">待识别</span>' : ""}
      ${conflict ? '<span class="chip red">冲突</span>' : ""}
      <span class="preview">${h(c.text.slice(0, 60))}</span>
      <span class="chars">${c.chars}字</span>
      <button class="btn small ghost cand-bind" title="搜索并选择学生账号">选择学生</button>
      ${c.submittedBy ? '<button class="btn small ghost cand-unbind" title="取消该作文的学生账号关联">取消关联</button>' : ""}
      <button class="btn small ghost cand-view" title="预览全文">预览</button>
      ${canEditStructure && i > 0 ? '<button class="btn small ghost move-up" title="上移">↑</button>' : ""}
      ${canEditStructure && i < candidates.length - 1 ? '<button class="btn small ghost move-down" title="下移">↓</button>' : ""}
      ${canEditStructure ? '<button class="btn small ghost split" title="按文中的小标题拆分成多篇">⇩ 拆分</button>' : ""}
      ${canEditStructure ? '<button class="btn small ghost ai-split" title="由 AI 识别边界并按原文位置切分">AI 拆分</button>' : ""}
      ${canEditStructure && i > 0 ? '<button class="btn small ghost merge" title="合并到上一篇">⇧ 合并</button>' : ""}
      <button class="btn small danger del" title="删除">✕</button>
    </div>`;
  }).join("");
  return `
    <div class="section-title">
      <h3>核对学生名单（${candidates.length} 篇）</h3>
      <span class="chip orange">请尽量关联学生账号；已关联的老师导入会在学生端锁定为已提交</span>
    </div>
    <div class="card" style="padding:14px 18px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
        <button class="btn small" id="can-auto-match">自动匹配姓名</button>
        <button class="btn small ${candidateFilter === "unbound" ? "primary" : "ghost"}" id="can-filter-unbound">只看未关联</button>
        <button class="btn small ${candidateFilter === "conflict" ? "primary" : "ghost"}" id="can-filter-conflict">只看冲突</button>
        <button class="btn small ${candidateFilter === "all" ? "primary" : "ghost"}" id="can-filter-all">显示全部</button>
        <button class="btn small ghost" id="can-clear-bindings">清空全部关联</button>
        <span class="chip gray">当前显示 ${visible.length} 篇</span>
      </div>
      ${rows || '<div class="empty" style="padding:26px 10px">没有符合条件的作文</div>'}
      <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:14px">
        <button class="btn" id="can-extract">AI 识别姓名</button>
        <button class="btn ghost" id="can-cancel">取消</button>
        <button class="btn primary" id="can-save">确认名单并保存（${candidates.length} 篇）</button>
      </div>
    </div>`;
}

async function saveCandidates() {
  syncCandidateRows();
  const list = candidates.map(c => ({
    studentName: c.studentName,
    studentTitle: c.studentTitle || "",
    submittedBy: c.submittedBy || "",
    text: c.text,
  }));
  if (!list.length) { toast("名单为空，请上传作文", "error"); return; }
  const studentUploads = new Map((cur.essays || [])
    .filter(e => e.submittedBy && e.submissionType === "student_upload")
    .map(e => [e.submittedBy, e]));
  const conflicts = list
    .filter(it => it.submittedBy && studentUploads.has(it.submittedBy))
    .map(it => it.studentName || it.submittedBy);
  if (conflicts.length) {
    const names = [...new Set(conflicts)].join("、");
    if (!confirm(`以下学生已有学生端提交：${names}\n继续保存会用老师导入版本覆盖并锁定，学生端不可再修改。确定继续？`)) {
      return;
    }
  }
  try {
    const r = await api(`/api/assignments/${cur.id}/essays`, {
      method: "POST",
      body: JSON.stringify({
        essays: list,
        uploadToken: candidatesUploadToken,
        overrideStudentSubmissions: conflicts.length > 0,
      }),
    });
    toast(`已保存 ${r.count} 篇作文，可以开始批改了`, "ok");
    candidates = null;
    candidatesUploadToken = "";
    localStorage.removeItem(`ocr-job:${cur.id}`);
    detailTab = "results";
    await openAssignment(cur.id);
  } catch (e) { toast(e.message, "error"); }
}

async function cancelCandidates() {
  const token = candidatesUploadToken;
  candidates = null;
  candidatesUploadToken = "";
  localStorage.removeItem(`ocr-job:${cur.id}`);
  if (token) {
    try {
      await api(`/api/assignments/${cur.id}/upload/cancel`, {
        method: "POST",
        body: JSON.stringify({ uploadToken: token }),
      });
    } catch (e) { /* 临时文件也会在过期清理时删除 */ }
  }
  renderDetail();
}

/* ---------- 拆分：把误合成一篇的作文按标题行拆开 ---------- */
const SPLIT_BLOCK_END = /[的了吗呢吧在里上下中天时后前来去们和与及是就都还]$/;
const SPLIT_TITLE_LINE = /^(《[^《》]{1,24}》|[^。！？，,；;：:、·—–]{2,10})$/;
const SPLIT_GLUED = /^([^。！？，,；;：:、·—–]{2,10})[，,].{25,}$/;

function splitCandidate(i) {
  syncCandidateRows();
  const c = candidates[i];
  const segs = [];
  let cur = "";
  const push = () => { if (cur.trim().length >= 10) segs.push(cur.trim()); cur = ""; };
  const hit = s => {
    if (cur.trim().length < 15) return false;
    if (SPLIT_TITLE_LINE.test(s)) return true;
    const m = s.match(SPLIT_GLUED);
    return !!(m && !SPLIT_BLOCK_END.test(m[1]));
  };
  // 行内《》标题（如 "……温暖的人。《难忘的比赛》上周五……"）也视为可拆分点
  for (const p of c.text.split(/(《[^《》]{1,24}》)/)) {
    if (/^《[^《》]{1,24}》$/.test(p)) {
      if (cur.trim().length >= 15) push();
      cur += (cur ? "\n" : "") + p;
      if (cur.trim().length >= 15) push();
    } else {
      for (const line of p.split("\n")) {
        const s = line.trim();
        if (!s) continue;
        if (hit(s)) push();
        cur += (cur ? "\n" : "") + s;
      }
    }
  }
  push();
  if (segs.length < 2) { toast("未发现可拆分的标题行", "info"); return; }
  const first = segs.shift();
  candidates[i] = { ...c, text: first, chars: first.length };
  segs.forEach((s, k) => {
    candidates.splice(i + 1 + k, 0, { studentName: `第${i + 2 + k}篇`, text: s, chars: s.length, submittedBy: "" });
  });
  renderDetail();
  toast(`已拆分为 ${segs.length + 1} 篇，请核对姓名`, "ok");
}

async function aiSplitCandidate(i) {
  syncCandidateRows();
  const candidate = candidates[i];
  if (!candidate) return;
  const button = document.querySelector(`.candidate-row[data-i="${i}"] .ai-split`);
  if (button) {
    button.disabled = true;
    button.textContent = "识别中…";
  }
  try {
    const result = await api(`/api/assignments/${cur.id}/ai-split`, {
      method: "POST",
      body: JSON.stringify({ studentName: candidate.studentName, text: candidate.text }),
    });
    if (!result.split || !result.essays || result.essays.length < 2) {
      toast("AI 判断当前内容只有一篇作文，无需切分", "info");
      if (button) { button.disabled = false; button.textContent = "AI 拆分"; }
      return;
    }
    const replacements = result.essays.map((essay, index) => {
      const studentName = essay.studentName || `第${i + index + 1}篇`;
      const studentNoHint = essay.studentNoHint || "";
      const matched = matchCandidateStudent(studentName, studentNoHint, candidateStudents);
      const keepOriginalBinding = index === 0 && studentName.trim() === candidate.studentName.trim();
      return {
        studentName,
        studentNoHint,
        text: essay.text,
        chars: essay.chars || essay.text.length,
        submittedBy: matched || (keepOriginalBinding ? candidate.submittedBy || "" : ""),
      };
    });
    candidates.splice(i, 1, ...replacements);
    renderDetail();
    toast(`AI 已按原文边界拆分为 ${replacements.length} 篇，请核对`, "ok");
  } catch (error) {
    toast(error.message, "error", 5200);
    if (button) { button.disabled = false; button.textContent = "AI 拆分"; }
  }
}

async function extractNames() {
  syncCandidateRows();
  const rows = document.querySelectorAll(".candidate-row");
  const items = [...rows].map(r => ({
    index: +r.dataset.i,
    studentName: candidates[+r.dataset.i].studentName,
    text: candidates[+r.dataset.i].text,
  }));
  if (!items.some(it => isPlaceholderName(it.studentName))) {
    toast("所有作文都已识别出姓名，无需 AI 识别", "info");
    return;
  }
  const btn = document.getElementById("can-extract");
  btn.disabled = true;
  btn.textContent = "AI 识别中…";
  try {
    const r = await api(`/api/assignments/${cur.id}/extract-names`, {
      method: "POST", body: JSON.stringify({ essays: items }),
    });
    r.names.forEach(n => {
      candidates[n.index].studentName = n.studentName;
      if (!candidates[n.index].submittedBy) {
        candidates[n.index].submittedBy = matchCandidateStudent(
          n.studentName, candidates[n.index].studentNoHint, candidateStudents);
      }
    });
    renderDetail();
    if (r.names.length) toast(`已识别 ${r.names.length} 个姓名，请核对`, "ok");
    else if (r.alreadyNamed === items.length) toast("所有作文都已识别出姓名，无需 AI 识别", "info");
    else toast("AI 未能识别出姓名，请预览全文后手动填写；必要时先拆分或合并", "error", 5200);
  } catch (e) {
    toast(e.message, "error", 4500);
  }
}

function autoMatchCandidates() {
  syncCandidateRows();
  let matched = 0;
  candidates.forEach(c => {
    const username = matchCandidateStudent(c.studentName, c.studentNoHint, candidateStudents);
    if (username && c.submittedBy !== username) {
      c.submittedBy = username;
      matched++;
    }
  });
  renderDetail();
  toast(matched ? `已自动匹配 ${matched} 篇` : "没有新的可匹配学生", matched ? "ok" : "info");
}

function openCandidateStudentModal(i) {
  syncCandidateRows();
  const c = candidates[i];
  if (!c) return;
  const renderList = query => {
    const q = (query || "").trim().toLowerCase();
    const items = candidateStudents.filter(s => {
      const hay = `${s.display_name || ""} ${s.studentNo || ""} ${s.username || ""} ${s.grade || ""} ${s.klass || ""}`.toLowerCase();
      return !q || hay.includes(q);
    });
    const box = document.getElementById("student-picker-list");
    box.innerHTML = items.map(s => `
      <button class="student-choice ${c.submittedBy === s.username ? "active" : ""}" data-username="${h(s.username)}">
        <b>${h(s.display_name)}</b>
        <span>学号 ${h(s.studentNo || s.username)} · 账号 ${h(s.username)}${s.grade || s.klass ? ` · ${h(s.grade)}${h(s.klass)}` : ""}</span>
      </button>`).join("") || '<div class="empty" style="padding:24px 10px">没有匹配的学生</div>';
    box.querySelectorAll(".student-choice").forEach(btn => btn.addEventListener("click", () => {
      candidates[i].submittedBy = btn.dataset.username;
      closeModal();
      renderDetail();
    }));
  };
  openModal(`
    <div class="modal-title">选择学生账号</div>
    <div class="picker-context">
      <div><b>作文姓名：</b>${h(c.studentName || "未命名")}</div>
      <div>${h(c.text.slice(0, 90))}</div>
    </div>
    <label class="field"><span>搜索姓名、学号、用户名、年级或班级</span>
      <input class="input" id="student-picker-query" value="${h(c.studentName || "")}" placeholder="输入姓名、学号、用户名"></label>
    <div class="student-picker-list" id="student-picker-list"></div>
    <div class="modal-actions">
      <button class="btn ghost" id="student-picker-clear">取消关联</button>
      <button class="btn ghost" onclick="closeModal()">关闭</button>
    </div>`);
  const q = document.getElementById("student-picker-query");
  q.addEventListener("input", () => renderList(q.value));
  document.getElementById("student-picker-clear").addEventListener("click", () => {
    candidates[i].submittedBy = "";
    closeModal();
    renderDetail();
  });
  renderList(q.value);
  q.focus();
  q.select();
}

/* ---------- 批改 ---------- */
function startGrade(force) {
  detailTab = "results";
  api(`/api/assignments/${cur.id}/grade`, { method: "POST", body: JSON.stringify({ force }) })
    .then(r => {
      if (force) toast("已开始全部重新批改", "ok");
      else if (r.started) toast(`已开始批改 ${r.started} 篇，预计 1-2 分钟`, "ok", 4000);
      else if (r.existing) toast(`${r.existing} 篇已在排队或批改中`, "info");
      else toast("没有待批改的作文", "info");
      startPolling();
    })
    .catch(e => toast(e.message, "error"));
}

function startPolling() {
  stopPolling();
  pollStartTs = Date.now();
  pollFinishedBase = (cur.essays || []).filter(e => ["done", "error"].includes(e.status)).length;
  const assignmentId = cur.id;
  const poll = async () => {
    try {
      if (!cur || cur.id !== assignmentId) return;
      const fresh = await api("/api/assignments/" + assignmentId);
      // await 期间用户可能切换作业/离开：响应只属于启动轮询时的那个作业。
      if (!cur || cur.id !== assignmentId) return;
      cur = fresh;
      renderDetail();
      const active = (cur.essays || []).some(e => ["queued", "grading"].includes(e.status));
      if (!active) {
        stopPolling();
        if (!cur || cur.id !== assignmentId) return;
        await loadAssignmentExtras();
        const failed = (cur.essays || []).filter(e => e.status === "error").length;
        const pending = (cur.essays || []).filter(e => e.status === "pending").length;
        const msg = pending ? `批改已停止：还有 ${pending} 篇待批改，可点击开始批改`
          : failed ? `批改结束：${failed} 篇失败，可逐篇重试` : "批改完成";
        toast(msg, failed ? "error" : "ok", 4000);
        renderDetail();
        await loadList();
        return;
      }
    } catch (e) {
      // 401 已触发页面跳转登录，停止轮询；其余瞬时错误忽略后继续。
      if (e && e.message === "登录已过期") { stopPolling(); return; }
    }
    pollTimer = setTimeout(poll, 1500);
  };
  pollTimer = setTimeout(poll, 400);
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

/* ---------- 事件绑定（renderDetail 后） ---------- */
document.addEventListener("click", async e => {
  if (!cur) return;
  const el = e.target.closest("#detail");
  if (!el) return;

  const detailTabButton = e.target.closest("[data-detail-tab]");
  if (detailTabButton) {
    detailTab = detailTabButton.dataset.detailTab;
    renderDetail();
    return;
  }

  if (e.target.closest("#insight-generate")) {
    const replace = Boolean(curInsight);
    if (replace && !confirm("重新生成会替换当前人工修改的共性问题，确定继续？")) return;
    const button = e.target.closest("#insight-generate");
    button.disabled = true;
    try {
      const result = await api(`/api/assignments/${cur.id}/analytics/insight/generate`, {
        method: "POST", body: JSON.stringify({ confirm: replace }),
      });
      curInsight = result.insight;
      renderDetail();
      toast("班级共性问题已生成", "ok");
    } catch (error) { toast(error.message, "error", 5200); }
    return;
  }
  if (e.target.closest("#insight-edit")) { openInsightEditModal(); return; }
  if (e.target.closest("#showcase-save")) {
    const essayIds = [...document.querySelectorAll(".showcase-check:checked")].map(item => item.value);
    const previous = (curShowcase || []).map(item => item.essayId).sort().join("|");
    const changed = previous !== essayIds.slice().sort().join("|");
    const resultsPublished = (cur.essays || []).some(item => item.resultStatus === "published");
    const needsConfirm = changed && (curShowcase.length > 0 || resultsPublished);
    if (needsConfirm && !confirm("成绩发布后修改优秀作文名单会记录审计，确定继续？")) return;
    try {
      await api(`/api/assignments/${cur.id}/showcase`, {
        method: "PUT", body: JSON.stringify({ essayIds, confirm: needsConfirm }),
      });
      await loadAssignmentExtras();
      renderDetail();
      toast("优秀作文名单已保存", "ok");
    } catch (error) { toast(error.message, "error"); }
    return;
  }

  if (e.target.closest("#bt-grade")) { startGrade(false); return; }
  if (e.target.closest("#bt-publish-results")) {
    if (!confirm("将当前待审核结果发布给学生？发布后会生成不可变历史记录。")) return;
    try {
      const result = await api(`/api/assignments/${cur.id}/publish-results`, {
        method: "POST", body: JSON.stringify({}),
      });
      toast(`已发布 ${result.count || 0} 篇结果`, "ok");
      await openAssignment(cur.id);
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (e.target.closest("#bt-reopen-all")) {
    if (!confirm("将允许所有已提交学生重新覆盖作文，确定继续？")) return;
    try {
      const result = await api(`/api/assignments/${cur.id}/submissions/reopen`, {
        method: "POST", body: JSON.stringify({}),
      });
      toast(`已开放 ${result.reopened.length} 名学生重交`, "ok");
      await openAssignment(cur.id);
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (e.target.closest("#bt-regrade-all")) {
    if (confirm("将全部重新批改。学生已看到的历史结果会保留，新结果需再次审核发布。确定继续？")) startGrade(true);
    return;
  }
  if (e.target.closest("#bt-export")) {
    if ((cur.essays || []).some(item => item.status === "done" && item.resultStatus === "pending_review")
        && !confirm("PDF 将包含老师最新保存但尚未再次发布的批改结果，确定导出？")) return;
    try {
      await downloadPdf(`/api/assignments/${cur.id}/export`);
      toast("报告已下载", "ok");
    } catch (err) { toast(err.message, "error"); }
    return;
  }
  if (e.target.closest("#bt-completion")) {
    try {
      await downloadPdf(`/api/assignments/${cur.id}/completion.xlsx`);
      toast("完成情况表已下载", "ok");
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  if (e.target.closest("#can-cancel")) { await cancelCandidates(); return; }
  if (e.target.closest("#can-save")) { saveCandidates(); return; }
  if (e.target.closest("#can-extract")) { extractNames(); return; }
  if (e.target.closest("#can-auto-match")) { autoMatchCandidates(); return; }
  if (e.target.closest("#can-filter-unbound")) { syncCandidateRows(); candidateFilter = "unbound"; renderDetail(); return; }
  if (e.target.closest("#can-filter-conflict")) { syncCandidateRows(); candidateFilter = "conflict"; renderDetail(); return; }
  if (e.target.closest("#can-filter-all")) { syncCandidateRows(); candidateFilter = "all"; renderDetail(); return; }
  if (e.target.closest("#can-clear-bindings")) {
    if (!confirm("确定清空当前名单里的全部学生账号关联？作文内容不会删除。")) return;
    syncCandidateRows();
    candidates.forEach(c => { c.submittedBy = ""; });
    renderDetail();
    return;
  }
  if (e.target.closest(".candidate-row .cand-bind")) {
    openCandidateStudentModal(+e.target.closest(".candidate-row").dataset.i);
    return;
  }
  if (e.target.closest(".candidate-row .cand-unbind")) {
    syncCandidateRows();
    candidates[+e.target.closest(".candidate-row").dataset.i].submittedBy = "";
    renderDetail();
    return;
  }
  if (e.target.closest(".candidate-row .del")) {
    syncCandidateRows();
    const row = e.target.closest(".candidate-row");
    candidates.splice(+row.dataset.i, 1);
    renderDetail();
    return;
  }
  if (e.target.closest(".candidate-row .split")) {
    splitCandidate(+e.target.closest(".candidate-row").dataset.i);
    return;
  }
  if (e.target.closest(".candidate-row .ai-split")) {
    aiSplitCandidate(+e.target.closest(".candidate-row").dataset.i);
    return;
  }
  if (e.target.closest(".candidate-row .cand-view")) {
    const i = +e.target.closest(".candidate-row").dataset.i;
    const c = candidates[i];
    openModal(`
      <div class="modal-title">第 ${i + 1} 篇预览　${h(c.studentName || "未命名")}</div>
      <details class="essay-text" open><summary>作文全文（${c.chars} 字）</summary>
        <div class="essay-preview">${h(c.text)}</div></details>
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button></div>`);
    return;
  }
  if (e.target.closest(".candidate-row .move-up")) {
    syncCandidateRows();
    const i = +e.target.closest(".candidate-row").dataset.i;
    if (i > 0) {
      [candidates[i - 1], candidates[i]] = [candidates[i], candidates[i - 1]];
      renderDetail();
    }
    return;
  }
  if (e.target.closest(".candidate-row .move-down")) {
    syncCandidateRows();
    const i = +e.target.closest(".candidate-row").dataset.i;
    if (i < candidates.length - 1) {
      [candidates[i], candidates[i + 1]] = [candidates[i + 1], candidates[i]];
      renderDetail();
    }
    return;
  }
  if (e.target.closest("#btn-source")) {
    try {
      await downloadPdf(`/api/assignments/${cur.id}/source`);
      toast("已下载原始文件", "ok");
    } catch (err) { toast(err.message, "error"); }
    return;
  }
  if (e.target.closest(".candidate-row .merge")) {
    syncCandidateRows();
    const row = e.target.closest(".candidate-row");
    const i = +row.dataset.i;
    candidates[i - 1].text += "\n" + candidates[i].text;
    candidates[i - 1].chars = candidates[i - 1].text.length;
    candidates.splice(i, 1);
    renderDetail();
    return;
  }
  const view = e.target.closest(".view");
  if (view) { showDetailModal(view.dataset.id); return; }
  const versions = e.target.closest(".versions");
  if (versions) { showVersionsModal(versions.dataset.id); return; }
  const trend = e.target.closest(".student-trend");
  if (trend) { showStudentTrendModal(trend.dataset.username); return; }
  const returnButton = e.target.closest(".return-submission");
  if (returnButton) { openReturnSubmissionModal(returnButton.dataset.id); return; }
  const publishResult = e.target.closest(".publish-result");
  if (publishResult) {
    try {
      await api(`/api/assignments/${cur.id}/essays/${publishResult.dataset.id}/publish-result`, {
        method: "POST",
      });
      toast("结果已发布给学生", "ok");
      await openAssignment(cur.id);
    } catch (error) { toast(error.message, "error"); }
    return;
  }
  const regrade = e.target.closest(".regrade");
  if (regrade) {
    const essay = (cur.essays || []).find(item => item.id === regrade.dataset.id);
    if (essay && essay.resultStatus === "published"
        && !confirm("重新批改不会删除学生已看到的旧结果；新结果需再次发布。确定继续？")) return;
    try {
      await api(`/api/assignments/${cur.id}/essays/${regrade.dataset.id}/regrade`, { method: "POST" });
      toast("已重新提交批改", "ok");
      startPolling();
    } catch (err) { toast(err.message, "error"); }
    return;
  }
  const exp = e.target.closest(".export");
  if (exp) {
    const essay = (cur.essays || []).find(item => item.id === exp.dataset.id);
    if (essay && essay.resultStatus === "pending_review"
        && !confirm("PDF 将包含老师最新保存但尚未再次发布的批改结果，确定导出？")) return;
    try {
      await downloadPdf(`/api/assignments/${cur.id}/export/${exp.dataset.id}`);
      toast("已下载该生批改意见", "ok");
    } catch (err) { toast(err.message, "error"); }
    return;
  }
  const reopen = e.target.closest(".reopen");
  if (reopen) {
    if (!confirm("开放后学生可以覆盖当前作文和批改结果，确定继续？")) return;
    try {
      await api(`/api/assignments/${cur.id}/submissions/${encodeURIComponent(reopen.dataset.username)}/reopen`, {
        method: "POST",
      });
      toast("已允许该学生重新提交", "ok");
      await openAssignment(cur.id);
    } catch (err) { toast(err.message, "error"); }
    return;
  }
});

function openReturnSubmissionModal(essayId) {
  const essay = (cur.essays || []).find(item => item.id === essayId);
  if (!essay) return;
  openModal(renderReturnSubmissionForm(essay));
  document.getElementById("return-confirm").addEventListener("click", async event => {
    const comment = document.getElementById("return-comment").value.trim();
    if (!comment) { toast("请填写退回评语", "error"); return; }
    event.currentTarget.disabled = true;
    try {
      await api(`/api/assignments/${cur.id}/essays/${essayId}/return`, {
        method: "POST", body: JSON.stringify({
          comment,
          resubmitDeadlineAt: dateTimeLocalToUtc(document.getElementById("return-deadline").value),
        }),
      });
      closeModal();
      toast("作文已退回，学生可提交下一稿", "ok");
      await openAssignment(cur.id);
    } catch (error) {
      toast(error.message, "error");
      event.currentTarget.disabled = false;
    }
  });
}

function cloneAssignmentModal() {
  if (!classes.length) { toast("请先联系管理员分配当前任教班级", "error"); return; }
  const fallback = classes.some(item => item.id === cur.classId) ? cur.classId : classes[0].id;
  openModal(`
    <div class="modal-title">复制作业为新草稿</div>
    <label class="field"><span>新作业名称</span><input class="input" id="clone-title" value="${h(cur.title)}（副本）"></label>
    <label class="field"><span>目标班级</span><select class="input" id="clone-class">${classes.map(item =>
      `<option value="${h(item.id)}" ${item.id === fallback ? "selected" : ""}>${h(item.name)}</option>`).join("")}</select></label>
    <div class="form-note">仅复制题目、要求、评分标准和字数，不复制学生、作文和截止时间。</div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="clone-confirm">创建草稿</button></div>`);
  document.getElementById("clone-confirm").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      const result = await api(`/api/assignments/${cur.id}/clone`, {
        method: "POST", body: JSON.stringify({
          title: document.getElementById("clone-title").value.trim(),
          classId: document.getElementById("clone-class").value,
        }),
      });
      closeModal();
      toast("新草稿已创建", "ok");
      await loadList();
      await openAssignment(result.id);
    } catch (error) { toast(error.message, "error"); event.currentTarget.disabled = false; }
  });
}

async function showVersionsModal(essayId) {
  try {
    const timeline = await api(`/api/assignments/${cur.id}/essays/${essayId}/versions`);
    openModal(renderRevisionTimeline(timeline));
    const cancelButton = document.querySelector(".cancel-return");
    if (cancelButton) cancelButton.addEventListener("click", async () => {
      if (!confirm("撤销退回后学生将不能再重交，确定继续？")) return;
      cancelButton.disabled = true;
      try {
        await api(`/api/assignments/${cur.id}/essays/${essayId}/return/cancel`, { method: "POST" });
        closeModal();
        toast("已撤销退回并恢复原稿状态", "ok");
        await openAssignment(cur.id);
      } catch (error) {
        toast(error.message, "error", 5000);
        cancelButton.disabled = false;
      }
    });
  } catch (error) { toast(error.message, "error"); }
}

async function showStudentTrendModal(username) {
  try {
    const result = await api(`/api/teacher/students/${encodeURIComponent(username)}/trends`);
    openModal(`
      <div class="modal-title">${h(result.student.displayName)} · 历次正式作业成绩</div>
      <div class="form-note">仅统计你发布并已向学生发布结果的作业，不包含自主练习。</div>
      ${result.assignments.length ? '<div class="chart-shell"><canvas id="teacher-trend-chart"></canvas></div>'
        : '<div class="empty compact-empty">暂无已发布成绩</div>'}
      <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button></div>`);
    if (!result.assignments.length) return;
    teacherTrendChart = new window.Chart(
      document.getElementById("teacher-trend-chart"),
      buildStudentTrendChartConfig(result.assignments),
    );
  } catch (error) { toast(error.message, "error"); }
}

function openInsightEditModal() {
  openModal(renderInsightEditForm(curInsight));
  document.getElementById("insight-save").addEventListener("click", async event => {
    const value = {};
    document.querySelectorAll(".insight-field").forEach(field => { value[field.dataset.key] = field.value.trim(); });
    if (Object.values(value).some(item => !item)) { toast("四项内容都需要填写", "error"); return; }
    event.currentTarget.disabled = true;
    try {
      const result = await api(`/api/assignments/${cur.id}/analytics/insight`, {
        method: "PUT", body: JSON.stringify({ insight: value, version: curInsight.version }),
      });
      curInsight = result.insight;
      closeModal();
      renderDetail();
      toast("共性问题已保存", "ok");
    } catch (error) {
      toast(error.message, "error");
      event.currentTarget.disabled = false;
    }
  });
}

function showDetailModal(eid) {
  const e = cur.essays.find(x => x.id === eid);
  if (!e) return;
  openModal(`
    <div class="modal-title">${h(e.studentName)}　${fmtScore(e.score)}　${h(e.level || "")}</div>
    <div class="form-note">作文标题：${h(e.studentTitle || cur.title || "未填写")}</div>
    ${e.feedbackEditedAt ? `<div class="form-note">老师最后修改：${h(e.feedbackEditedBy || "-")} · ${h(formatLocalDateTime(e.feedbackEditedAt))}</div>` : ""}
    <div id="fb-box"></div>
    <details class="essay-text"><summary>查看作文原文（${e.chars} 字）</summary>
      <div class="essay-preview">${h(e.text)}</div></details>
    <div class="modal-actions">
      <button class="btn" id="fb-upgrade" data-id="${h(e.id)}">生成润色提升版</button>
      <button class="btn primary" id="fb-edit" data-id="${h(e.id)}">编辑批改结果</button>
      <button class="btn ghost" onclick="closeModal()">关闭</button>
    </div>`);
  renderFeedback(document.getElementById("fb-box"), { ...e.feedback, gradedAt: e.gradedAt });
  document.getElementById("fb-edit").addEventListener("click", () => showFeedbackEditModal(e.id));
  document.getElementById("fb-upgrade").addEventListener("click", async event => {
    const btn = event.currentTarget;
    btn.disabled = true;
    btn.textContent = "正在生成...";
    try {
      const r = await api(`/api/assignments/${cur.id}/essays/${e.id}/upgrade`, { method: "POST" });
      e.feedback.upgrade = r.upgrade;
      renderFeedback(document.getElementById("fb-box"), { ...e.feedback, gradedAt: e.gradedAt });
      toast(r.cached ? "已显示润色提升版" : "润色提升版已生成", "ok");
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      btn.disabled = false;
      btn.textContent = "生成润色提升版";
    }
  });
}

function showFeedbackEditModal(eid) {
  const essay = (cur.essays || []).find(item => item.id === eid);
  if (!essay || !essay.feedback) return;
  const feedback = essay.feedback;
  const dimensions = feedback.dimensions || {};
  const currentGrade = ["优", "良", "中", "差"].includes(feedback.level)
    ? feedback.level : defaultFeedbackGrade(feedback.score);
  openModal(`
    <form id="feedback-edit-form">
      <div class="modal-title">编辑批改结果 · ${h(essay.studentName)}</div>
      <div class="form-note">保存采用版本校验。已发布结果修改后，学生仍看到旧版本，直到再次发布。</div>
      <div class="feedback-edit-scores">
        <label class="field"><span>总分</span><input class="input" id="fe-score" type="number" min="0" max="100" value="${h(feedback.score)}" required></label>
        <label class="field"><span>等第</span><select class="input" id="fe-level">
          ${["优", "良", "中", "差"].map(value => `<option ${value === currentGrade ? "selected" : ""}>${value}</option>`).join("")}
        </select></label>
        ${["立意与内容", "结构与逻辑", "语言表达"].map((key, index) => `
          <label class="field"><span>${key}</span><input class="input fe-dimension" data-key="${key}" type="number" min="0" max="100" value="${h(dimensions[key] ?? 0)}" required></label>`).join("")}
      </div>
      <label class="field"><span>总评</span><textarea class="input" id="fe-summary" rows="3">${h(feedback.summary || "")}</textarea></label>
      <label class="field"><span>优点（每行一条）</span><textarea class="input" id="fe-strengths" rows="4">${h(feedbackListText(feedback.strengths))}</textarea></label>
      <label class="field"><span>不足（每行一条）</span><textarea class="input" id="fe-weaknesses" rows="4">${h(feedbackListText(feedback.weaknesses))}</textarea></label>
      <label class="field"><span>修改建议（每行一条）</span><textarea class="input" id="fe-suggestions" rows="4">${h(feedbackListText(feedback.suggestions))}</textarea></label>
      <label class="field"><span>示范句</span><textarea class="input" id="fe-model" rows="3">${h(feedback.modelSnippet || "")}</textarea></label>
      <label class="field"><span>整篇提升版</span><textarea class="input" id="fe-upgrade" rows="8">${h(feedback.upgrade || "")}</textarea></label>
      <div class="modal-actions">
        <button class="btn ghost" type="button" id="fe-cancel">取消</button>
        <button class="btn primary" type="submit" id="fe-save">保存修改</button>
      </div>
    </form>`, { dismissible: true });
  document.getElementById("fe-cancel").addEventListener("click", () => closeModal());
  document.getElementById("fe-score").addEventListener("input", event => {
    document.getElementById("fe-level").value = defaultFeedbackGrade(event.target.value);
  });
  document.getElementById("feedback-edit-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = document.getElementById("fe-save");
    if (button.disabled) return;
    const lineList = id => document.getElementById(id).value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    const dims = {};
    document.querySelectorAll(".fe-dimension").forEach(input => { dims[input.dataset.key] = Number(input.value); });
    const payload = {
      feedbackVersion: Number(essay.feedbackVersion || 0),
      feedback: {
        score: Number(document.getElementById("fe-score").value),
        level: document.getElementById("fe-level").value,
        dimensions: dims,
        summary: document.getElementById("fe-summary").value.trim(),
        strengths: lineList("fe-strengths"),
        weaknesses: lineList("fe-weaknesses"),
        suggestions: lineList("fe-suggestions"),
        modelSnippet: document.getElementById("fe-model").value.trim(),
        upgrade: document.getElementById("fe-upgrade").value.trim(),
      },
    };
    button.disabled = true;
    setModalDismissible(false);
    try {
      const result = await api(`/api/assignments/${cur.id}/essays/${essay.id}/feedback`, {
        method: "PUT", body: JSON.stringify(payload),
      });
      setModalDismissible(true);
      closeModal();
      toast(result.unchanged ? "内容没有变化" : result.wasPublished
        ? "修改已保存，结果已回到待发布" : "批改结果已保存", "ok");
      await openAssignment(cur.id);
    } catch (error) {
      setModalDismissible(true);
      button.disabled = false;
      toast(error.message, "error", 5200);
    }
  });
}
