/* ============ 学生：历史记录 ============ */
import {
  api, downloadPdf, formatLocalDateTime, h, openModal, pageInit,
  renderFeedback, toast,
} from "./shared/index.js";

let historyType = "assignment";
let historyPage = 1;
let historyItems = [];
let historyTotal = 0;
const historyPageSize = 10;
let growthChart = null;

pageInit(["student"], async () => {
  document.querySelectorAll(".segmented button").forEach(button => {
    button.addEventListener("click", async () => {
      historyType = button.dataset.type;
      historyPage = 1;
      document.querySelectorAll(".segmented button").forEach(item => {
        item.classList.toggle("active", item === button);
      });
      await loadHistoryPage();
    });
  });
  await Promise.all([loadHistoryPage(), loadGrowthChart()]);
});

async function loadGrowthChart() {
  if (!window.features?.analytics?.available) {
    document.querySelector(".growth-chart-panel").hidden = true;
    return;
  }
  const trends = await api("/api/student/trends");
  const points = [
    ...(trends.assignments || []).map(item => ({ ...item, type: "assignment" })),
    ...(trends.practice || []).map(item => ({ ...item, type: "practice" })),
  ].filter(item => Number.isFinite(Number(item.score)))
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || a.type.localeCompare(b.type));
  const canvas = document.getElementById("growth-chart");
  document.getElementById("growth-empty").hidden = points.length > 0;
  canvas.hidden = points.length === 0;
  if (!points.length) return;
  const labels = points.map(item => `${formatLocalDateTime(item.date).slice(0, 10)} · ${item.label}`);
  growthChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "老师作业",
          data: points.map(item => item.type === "assignment" ? Number(item.score) : null),
          borderColor: "#2563eb", backgroundColor: "#2563eb", spanGaps: true,
          pointRadius: 4, pointHoverRadius: 6, tension: 0.2,
        },
        {
          label: "自主练习",
          data: points.map(item => item.type === "practice" ? Number(item.score) : null),
          borderColor: "#16a34a", backgroundColor: "#16a34a", spanGaps: true,
          pointRadius: 4, pointHoverRadius: 6, tension: 0.2,
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        y: { min: 0, max: 100, ticks: { stepSize: 20 } },
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } },
      },
    },
  });
}

async function loadHistoryPage() {
  const query = new URLSearchParams({
    type: historyType,
    page: String(historyPage),
    pageSize: String(historyPageSize),
  });
  const response = await api(`/api/student/history?${query}`);
  historyItems = response.history || [];
  historyTotal = response.total || 0;
  renderHistoryList();
  renderHistoryPagination();
  document.getElementById("history-detail").hidden = true;
  document.getElementById("history-placeholder").hidden = false;
}

function renderHistoryList() {
  const box = document.getElementById("history-list");
  if (!historyItems.length) {
    box.innerHTML = `<div class="empty compact-empty">${historyType === "assignment" ? "暂无老师作业记录" : "暂无自主练习记录"}</div>`;
    return;
  }
  box.innerHTML = historyItems.map(item => `
    <button class="history-row" data-id="${h(item.id)}">
      <span class="history-row-main">
        <b>${h(item.assignmentTitle || item.title || "（无标题）")}</b>
        <small>${h(item.gradedAt)} · ${h(item.level || "")}${item.publicationNo > 1 ? ` · 第 ${item.publicationNo} 次发布` : ""}</small>
      </span>
      <span class="score ${item.score >= 80 ? "high" : item.score >= 70 ? "low" : "bad"}">${item.score} 分</span>
    </button>`).join("");
  box.querySelectorAll(".history-row").forEach(row => {
    row.addEventListener("click", () => showHistoryDetail(row.dataset.id));
  });
}

function renderHistoryPagination() {
  const pages = Math.max(1, Math.ceil(historyTotal / historyPageSize));
  const box = document.getElementById("history-pagination");
  box.innerHTML = `
    <button class="btn small ghost" id="history-prev" ${historyPage <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${historyPage} / ${pages} 页，共 ${historyTotal} 条</span>
    <button class="btn small ghost" id="history-next" ${historyPage >= pages ? "disabled" : ""}>下一页</button>`;
  document.getElementById("history-prev").addEventListener("click", async () => {
    historyPage -= 1;
    await loadHistoryPage();
  });
  document.getElementById("history-next").addEventListener("click", async () => {
    historyPage += 1;
    await loadHistoryPage();
  });
}

function showHistoryDetail(id) {
  const item = historyItems.find(entry => entry.id === id);
  if (!item) return;
  document.querySelectorAll(".history-row").forEach(row => {
    row.classList.toggle("active", row.dataset.id === id);
  });
  document.getElementById("history-placeholder").hidden = true;
  const detail = document.getElementById("history-detail");
  detail.hidden = false;
  detail.innerHTML = `
    <div class="pane-head result-head">
      <div><h2>${h(item.assignmentTitle || item.title || "（无标题）")}</h2><p>${h(item.gradedAt)}</p></div>
      <div class="ops">
        <button class="btn small" id="history-export">导出 PDF</button>
        <button class="btn small ghost" id="history-upgrade">生成润色提升版</button>
      </div>
    </div>
    <div id="history-feedback"></div>
    ${item.assignmentId && item.sourceEssayId ? '<section class="revision-inline"><div class="section-strip"><b>提交版本</b><button class="btn small ghost" id="history-versions">查看全部稿件与退回评语</button></div><div id="history-version-list"></div></section>' : ""}
    <details class="essay-text"><summary>查看本次发布的作文原文</summary><div class="essay-preview">${h(item.text || "")}</div></details>`;
  renderFeedback(document.getElementById("history-feedback"), { ...item.result, gradedAt: item.gradedAt });
  document.getElementById("history-export").addEventListener("click", async () => {
    try {
      await downloadPdf(`/api/student/export/${item.id}`);
      toast("批改意见 PDF 已下载", "ok");
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("history-upgrade").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在生成...";
    try {
      const response = await api(`/api/student/history/${item.id}/upgrade`, { method: "POST" });
      item.result.upgrade = response.upgrade;
      renderFeedback(document.getElementById("history-feedback"), { ...item.result, gradedAt: item.gradedAt });
      toast(response.cached ? "已显示润色提升版" : "润色提升版已生成", "ok");
    } catch (error) {
      toast(error.message, "error", 5000);
    } finally {
      button.disabled = false;
      button.textContent = "生成润色提升版";
    }
  });
  const versions = document.getElementById("history-versions");
  if (versions) versions.addEventListener("click", async () => {
    versions.disabled = true;
    try {
      const timeline = await api(`/api/assignments/${item.assignmentId}/essays/${item.sourceEssayId}/versions`);
      document.getElementById("history-version-list").innerHTML = renderRevisionTimeline(timeline);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      versions.disabled = false;
    }
  });
  if (window.matchMedia("(max-width: 760px)").matches) {
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderRevisionTimeline(timeline) {
  const returns = new Map((timeline.returns || []).map(item => [item.versionId, item]));
  return `<div class="revision-timeline">${(timeline.versions || []).map(version => {
    const returned = returns.get(version.id);
    return `<article class="revision-entry">
      <div class="revision-marker"></div>
      <div><h3>第 ${version.versionNo} 稿 <span class="chip gray">${h(version.status)}</span></h3>
        <p>${h(formatLocalDateTime(version.submittedAt))}${version.score != null ? ` · ${version.score} 分` : ""}</p>
        <details><summary>查看本稿正文</summary><div class="essay-preview">${h(version.text || "")}</div></details>
        ${returned ? `<div class="return-note"><b>老师退回评语</b><p>${h(returned.comment)}</p>
          <small>${h(formatLocalDateTime(returned.returnedAt))}${returned.resubmitDeadlineAt ? ` · 重交截止 ${h(formatLocalDateTime(returned.resubmitDeadlineAt))}` : " · 未设重交截止"}</small></div>` : ""}
      </div>
    </article>`;
  }).join("")}</div>`;
}
