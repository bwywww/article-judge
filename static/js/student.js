/* ============ 学生：完成老师作业 ============ */
import {
  api, downloadPdf, formatLocalDateTime, h, openModal, pageInit, pollStudentJob,
  renderFeedback, toast,
} from "./shared/index.js";

let curResult = null;
let stuAssignments = [];
let assignmentArchive = { current: [], historical: [] };
let assignmentView = "pending";
let selectedAssignmentId = null;
const monitoredAssignmentJobs = new Set();

pageInit(["student"], async () => {
  const text = document.getElementById("st-text");
  text.addEventListener("input", updateCount);
  updateCount();

  document.querySelectorAll("[data-assignment-view]").forEach(button => {
    button.addEventListener("click", () => {
      assignmentView = button.dataset.assignmentView;
      document.querySelectorAll("[data-assignment-view]").forEach(item =>
        item.classList.toggle("active", item === button));
      selectedAssignmentId = null;
      document.getElementById("submit-card").hidden = true;
      renderAssignmentList();
    });
  });
  await loadAssignments();
  wireStudentForm();
  resumeAssignmentJobs();
});

function wireStudentForm() {
  document.getElementById("st-upload").addEventListener("click", () => document.getElementById("st-file").click());
  document.getElementById("st-file").addEventListener("change", async () => {
    const file = document.getElementById("st-file").files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    toast(`正在读取 ${file.name} ...`);
    try {
      const r = await api("/api/student/parse", { method: "POST", body: fd });
      document.getElementById("st-text").value = r.text;
      updateCount();
      toast("已载入作文内容", "ok");
    } catch (e) {
      toast(e.message, "error", 5000);
    }
    document.getElementById("st-file").value = "";
  });

  document.getElementById("st-grade").addEventListener("click", submitGrade);
  document.getElementById("st-upgrade").addEventListener("click", generateUpgrade);
  document.getElementById("st-export").addEventListener("click", async () => {
    if (!curResult || !curResult.historyId) return;
    try {
      await downloadPdf(`/api/student/export/${curResult.historyId}`);
      toast("批改意见 PDF 已下载", "ok");
    } catch (e) { toast(e.message, "error"); }
  });
}

function updateCount() {
  const n = document.getElementById("st-text").value.length;
  document.getElementById("st-count").textContent = n + " 字";
}

async function loadAssignments() {
  const r = await api("/api/assignments");
  stuAssignments = r.assignments || [];
  if (window.features?.academic_years?.available) {
    assignmentArchive = await api("/api/student/assignments/archive");
  }
  renderAssignmentList();
}

function visibleAssignments() {
  if (assignmentView === "historical") {
    return (assignmentArchive.historical || []).map(item => ({
      ...item, historical: true, submissionStatus: item.resultStatus === "published"
        ? "result_published" : item.status === "not_submitted" ? "not_submitted" : "student_submitted_locked",
      submissionLocked: true, canSubmit: false,
    }));
  }
  if (assignmentView === "current") return stuAssignments;
  return stuAssignments.filter(item => item.canSubmit &&
    ["not_submitted", "resubmission_open"].includes(item.submissionStatus))
    .sort((a, b) => {
      const aUrgent = a.submissionStatus === "not_submitted" && !a.deadlineAt ? 0 : 1;
      const bUrgent = b.submissionStatus === "not_submitted" && !b.deadlineAt ? 0 : 1;
      return aUrgent - bUrgent || String(a.deadlineAt || "9999").localeCompare(String(b.deadlineAt || "9999"));
    });
}

function renderAssignmentList() {
  const list = document.getElementById("assignments-list");
  const items = visibleAssignments();
  const titles = { pending: "待完成", current: "本班记录", historical: "历史学年" };
  document.getElementById("assignment-list-title").textContent = titles[assignmentView];
  document.getElementById("assignment-list-count").textContent = `${items.length} 项`;
  if (!items.length) {
    const empty = assignmentView === "pending" ? "目前没有待完成作业" :
      assignmentView === "historical" ? "暂无历史学年作业" : "暂无本班作业记录";
    list.innerHTML = `<div class="empty" style="padding:30px 10px">${empty}</div>`;
    document.getElementById("submit-card").hidden = true;
    return;
  }
  list.innerHTML = items.map(a => `
    <button class="history-item assignment-row ${selectedAssignmentId === a.id ? "active" : ""}
      ${a.submissionStatus === "not_submitted" && !a.deadlineAt ? "assignment-urgent" : ""}" data-id="${h(a.id)}" data-historical="${a.historical ? "1" : "0"}">
      <div class="t"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h(a.title)}</span>
        ${a.wordLimit ? `<span class="chip gray">${h(a.wordLimit)}</span>` : ""}
        ${a.className ? `<span class="chip blue">${h(a.className)}</span>` : ""}
        ${a.historical ? `<span class="chip gray">${h(a.className || "历史班级")}</span>` : ""}
        ${a.lifecycleStatus === "closed" || a.lifecycleStatus === "archived" ? '<span class="chip gray">已结束</span>' : ""}
        ${assignmentChip(a)}</div>
      <div class="m">${h(a.prompt || (a.historical ? "历史作业只读" : "（暂无要求说明）"))}${a.deadlineAt ? ` · 截止 ${h(formatLocalDateTime(a.deadlineAt))}` : a.submissionStatus === "not_submitted" ? " · 未设置截止时间" : ""}${a.submittedAt ? ` · 提交 ${h(a.submittedAt)}` : ""}</div>
    </button>`).join("");
  list.querySelectorAll(".history-item").forEach(item => {
    item.addEventListener("click", () => pickAssignment(item.dataset.id));
  });
  renderSelectedChip();
}

function assignmentChip(a) {
  if (a.submissionStatus === "queued") return '<span class="chip orange">排队中</span>';
  if (a.submissionStatus === "grading") return '<span class="chip orange">批改中</span>';
  if (a.submissionStatus === "pending_review") return '<span class="chip blue">等待老师发布</span>';
  if (a.submissionStatus === "result_published") return `<span class="chip green">结果已发布${a.publishedScore != null ? ` · ${h(a.publishedScore)} 分` : ""}</span>`;
  if (a.submissionStatus === "failed") return '<span class="chip red">批改失败</span>';
  if (a.submissionStatus === "teacher_submitted_locked") return '<span class="chip green">已提交（老师已导入）</span>';
  if (a.submissionStatus === "student_submitted_locked") return '<span class="chip green">已提交（已锁定）</span>';
  if (a.submissionStatus === "resubmission_open") return '<span class="chip orange">老师已退回，请重交</span>';
  if (a.submissionStatus === "resubmission_expired") return '<span class="chip red">重交期限已过</span>';
  return '<span class="chip gray">未提交</span>';
}

function pickAssignment(id) {
  const a = visibleAssignments().find(x => x.id === id) || stuAssignments.find(x => x.id === id);
  if (!a) return;
  if (a.historical) {
    selectedAssignmentId = null;
    document.getElementById("submit-card").hidden = true;
    showAssignmentPlaceholder("历史学年作业已冻结，可到成长记录查看已发布结果。", a.title, true);
    return;
  }
  if (!a.canSubmit && !a.submissionLocked) {
    selectedAssignmentId = null;
    document.getElementById("submit-card").hidden = true;
    showAssignmentPlaceholder("作业已关闭，当前不能提交。", a.title);
    renderSelectedChip();
    return;
  }
  if (a.submissionLocked) {
    selectedAssignmentId = null;
    document.getElementById("submit-card").hidden = true;
    const messages = {
      queued: "作文已提交，正在等待批改。",
      grading: "作文正在批改，关闭页面不会中断任务。",
      pending_review: "批改已完成，等待老师审核并发布结果。",
      result_published: "结果已发布，可在历史记录中查看。",
      failed: "本次批改失败，请联系老师处理或重新开放提交。",
      resubmission_expired: "老师曾退回本次作文，但重交期限已经结束。",
    };
    showAssignmentPlaceholder(
      messages[a.submissionStatus] || "该作业已提交并锁定。需要修改时请联系老师重新开放。",
      a.title, a.submissionStatus === "result_published");
    if (a.submissionStatus === "result_published" && window.features?.showcase?.available) {
      loadAssignmentShowcase(a.id);
    }
    renderSelectedChip();
    toast("该作业已提交并锁定；需要修改时请联系老师重新开放", "info", 4200);
    return;
  }
  selectedAssignmentId = id;
  document.getElementById("submit-card").hidden = false;
  document.getElementById("assignment-placeholder").hidden = true;
  document.getElementById("st-theme").value = a.title || "";
  document.getElementById("st-title").value = a.studentTitle || "";
  document.getElementById("st-prompt").value = a.prompt || "";
  const returnBox = document.getElementById("return-guidance");
  if (returnBox) returnBox.remove();
  if (a.submissionStatus === "resubmission_open" && a.returnComment) {
    document.getElementById("submit-card").insertAdjacentHTML("afterbegin", `
      <div class="return-note" id="return-guidance"><b>老师退回评语</b><p>${h(a.returnComment)}</p>
      <small>${a.resubmitDeadlineAt ? `请在 ${h(formatLocalDateTime(a.resubmitDeadlineAt))} 前重新提交` : "本次重交未设置截止时间"}</small></div>`);
  }
  renderAssignmentList();
  toast(`已选择作业《${a.title}》，写完提交即可`, "ok");
}

function renderSelectedChip() {
  const el = document.getElementById("selected-chip");
  if (!el) return;
  const a = stuAssignments.find(x => x.id === selectedAssignmentId);
  el.textContent = a ? `当前作业：${a.title}` : "";
}

async function submitGrade() {
  const selected = stuAssignments.find(x => x.id === selectedAssignmentId);
  if (!selected) { toast("请先选择一项老师作业", "error"); return; }
  if (selected.submissionLocked) {
    toast("该作业已提交并锁定；需要修改时请联系老师重新开放", "error", 4200);
    return;
  }
  const title = document.getElementById("st-title").value.trim();
  const prompt = document.getElementById("st-prompt").value.trim();
  const text = document.getElementById("st-text").value.trim();
  if (text.length < 10) { toast("请先粘贴或上传作文内容", "error"); return; }
  const btn = document.getElementById("st-grade");
  btn.disabled = true;
  btn.textContent = "正在提交...";
  try {
    const r = await api("/api/student/grade", {
      method: "POST",
      body: JSON.stringify({ title, prompt, text, assignmentId: selectedAssignmentId }),
    });
    localStorage.setItem(`assignmentJob:${selected.id}`, r.jobId);
    toast(r.existing ? "已恢复正在进行的批改任务" : "作文已提交并进入批改队列", "ok", 3200);
    await monitorAssignmentJob(r.jobId, selected.id, selected.title);
  } catch (e) {
    toast(e.message, "error", 5000);
    btn.disabled = false;
    btn.textContent = "提交并批改";
  }
}

function resumeAssignmentJobs() {
  stuAssignments
    .filter(item => ["queued", "grading"].includes(item.submissionStatus) && item.jobId)
    .forEach(item => monitorAssignmentJob(item.jobId, item.id, item.title));
}

async function monitorAssignmentJob(jobId, assignmentId, assignmentTitle) {
  if (monitoredAssignmentJobs.has(jobId)) return;
  monitoredAssignmentJobs.add(jobId);
  const button = document.getElementById("st-grade");
  try {
    const job = await pollStudentJob(jobId, async update => {
      if (button && selectedAssignmentId === assignmentId) {
        button.disabled = true;
        button.textContent = update.status === "queued" ? "排队中..." : "AI 批改中...";
      }
      await loadAssignments();
    });
    localStorage.removeItem(`assignmentJob:${assignmentId}`);
    selectedAssignmentId = null;
    document.getElementById("submit-card").hidden = true;
    if (job.status === "pending_review") {
      showAssignmentPlaceholder("批改已完成，等待老师审核并发布结果。", assignmentTitle);
      toast("批改已完成，等待老师发布结果", "ok", 3600);
    } else if (job.status === "error") {
      showAssignmentPlaceholder(job.errorAdvice || "本次批改失败，请联系老师处理。", assignmentTitle);
      toast(job.error || "批改失败", "error", 5200);
    }
    await loadAssignments();
  } catch (error) {
    toast(error.message, "error", 5000);
  } finally {
    monitoredAssignmentJobs.delete(jobId);
    if (button) {
      button.disabled = false;
      button.textContent = "提交并批改";
    }
  }
}

function showAssignmentPlaceholder(message, title = "", showHistory = false) {
  const box = document.getElementById("assignment-placeholder");
  box.hidden = false;
  box.innerHTML = `<h2>${h(title || "选择一项作业")}</h2><p>${h(message)}</p>
    ${showHistory ? '<a class="btn primary" href="/student/history">查看批改结果</a>' : ""}`;
}

async function loadAssignmentShowcase(assignmentId) {
  try {
    const result = await api(`/api/assignments/${assignmentId}/showcase`);
    if (!result.items?.length) return;
    const box = document.getElementById("assignment-placeholder");
    box.insertAdjacentHTML("beforeend", `<button class="btn" id="student-showcase">查看本次优秀作文（${result.items.length} 篇）</button>`);
    document.getElementById("student-showcase").addEventListener("click", () => {
      openModal(`<div class="modal-title">本次优秀作文</div>
        <div class="student-showcase-list">${result.items.map(item => `<article>
          <div class="section-strip"><div><b>${h(item.studentName)} · ${h(item.title)}</b><span>本次小红花</span></div>
            <span class="chip red showcase-flower">小红花</span></div>
          <div class="essay-preview">${h(item.text)}</div>
        </article>`).join("")}</div>
        <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button></div>`);
    });
  } catch (error) { /* 作业暂无优秀作文时不打断主流程 */ }
}

async function generateUpgrade() {
  if (!curResult || !curResult.historyId) return;
  const btn = document.getElementById("st-upgrade");
  btn.disabled = true;
  btn.textContent = "正在生成...";
  try {
    const r = await api(`/api/student/history/${curResult.historyId}/upgrade`, { method: "POST" });
    curResult.upgrade = r.upgrade;
    renderFeedback(document.getElementById("st-result"), curResult);
    toast(r.cached ? "已显示润色提升版" : "润色提升版已生成", "ok");
  } catch (e) {
    toast(e.message, "error", 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = "生成润色提升版";
  }
}

function showResult(result, historyId) {
  curResult = { ...result, historyId };
  document.getElementById("result-area").hidden = false;
  renderFeedback(document.getElementById("st-result"), result);
  document.getElementById("result-area").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
