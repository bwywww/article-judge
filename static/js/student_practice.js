/* ============ 学生：自主完整练习 ============ */
import { api, downloadPdf, pageInit, pollStudentJob, renderFeedback, toast } from "./shared/index.js";

let curResult = null;
let monitoredPracticeJob = "";

pageInit(["student"], async () => {
  const text = document.getElementById("st-text");
  text.addEventListener("input", updateCount);
  updateCount();

  document.getElementById("st-upload").addEventListener("click", () => document.getElementById("st-file").click());
  document.getElementById("st-file").addEventListener("change", async () => {
    const file = document.getElementById("st-file").files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    toast(`正在读取 ${file.name} ...`);
    try {
      const r = await api("/api/student/parse", { method: "POST", body: fd });
      text.value = r.text;
      updateCount();
      toast("已载入作文内容", "ok");
    } catch (e) {
      toast(e.message, "error", 5000);
    }
    document.getElementById("st-file").value = "";
  });

  document.getElementById("st-grade").addEventListener("click", submitPractice);
  document.getElementById("st-upgrade").addEventListener("click", generateUpgrade);
  document.getElementById("st-export").addEventListener("click", async () => {
    if (!curResult || !curResult.historyId) return;
    try {
      await downloadPdf(`/api/student/export/${curResult.historyId}`);
      toast("批改意见 PDF 已下载", "ok");
    } catch (e) { toast(e.message, "error"); }
  });
  const pendingJob = localStorage.getItem("practiceJob");
  if (pendingJob) monitorPracticeJob(pendingJob);
});

function updateCount() {
  const n = document.getElementById("st-text").value.length;
  document.getElementById("st-count").textContent = n + " 字";
}

async function submitPractice() {
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
      body: JSON.stringify({ title, prompt, text, assignmentId: "" }),
    });
    localStorage.setItem("practiceJob", r.jobId);
    toast(r.existing ? "已恢复正在进行的批改任务" : "练习已进入批改队列", "ok", 3200);
    await monitorPracticeJob(r.jobId);
  } catch (e) {
    toast(e.message, "error", 5000);
    btn.disabled = false;
    btn.textContent = "开始批改";
  }
}

async function monitorPracticeJob(jobId) {
  if (!jobId || monitoredPracticeJob === jobId) return;
  monitoredPracticeJob = jobId;
  const button = document.getElementById("st-grade");
  try {
    const job = await pollStudentJob(jobId, update => {
      button.disabled = true;
      button.textContent = update.status === "queued" ? "排队中..." : "AI 批改中...";
    });
    localStorage.removeItem("practiceJob");
    if (job.status === "done") {
      showResult(job.result || {}, job.historyId);
      toast("批改完成", "ok", 3200);
    } else if (job.status === "error") {
      toast(`${job.error || "批改失败"}${job.errorAdvice ? `：${job.errorAdvice}` : ""}`, "error", 6000);
    }
  } catch (error) {
    toast(error.message, "error", 5000);
  } finally {
    monitoredPracticeJob = "";
    button.disabled = false;
    button.textContent = "开始批改";
  }
}

function showResult(result, historyId) {
  curResult = { ...result, historyId };
  document.getElementById("result-area").hidden = false;
  renderFeedback(document.getElementById("st-result"), result);
  document.getElementById("result-area").scrollIntoView({ behavior: "smooth", block: "nearest" });
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
