import { fmtScore, formatLocalDateTime, h } from "../shared/index.js";

export function renderAnalyticsPanel(assignment, currentInsight, currentShowcase) {
  const insight = currentInsight?.insight || {};
  const selected = new Set((currentShowcase || []).map(item => item.essayId));
  const eligible = (assignment.essays || [])
    .filter(item => item.status === "done" && item.submittedBy);
  return `
    <div class="analytics-grid">
      <section class="data-surface analytics-section">
        <div class="section-strip"><div><b>本次共性问题</b><span>只汇总结构化批改意见，不发送作文原文</span></div>
          <div class="ops"><button class="btn small" id="insight-generate">${currentInsight ? "重新生成" : "AI 汇总"}</button>
            ${currentInsight ? '<button class="btn small primary" id="insight-edit">编辑保存</button>' : ""}</div></div>
        ${currentInsight ? `<div class="insight-sections">
          <div><h3>整体表现</h3><p>${h(insight.overall || "")}</p></div>
          <div><h3>共同优点</h3><p>${h(insight.commonStrengths || "")}</p></div>
          <div><h3>常见问题</h3><p>${h(insight.commonProblems || "")}</p></div>
          <div><h3>教学建议</h3><p>${h(insight.teachingSuggestions || "")}</p></div>
          <small>版本 ${currentInsight.version} · ${h(formatLocalDateTime(currentInsight.editedAt || currentInsight.generatedAt))}</small>
        </div>` : '<div class="empty compact-empty">批改完成后可生成班级共性问题，内容仅老师可见。</div>'}
      </section>
      <section class="data-surface analytics-section" data-feature="showcase">
        <div class="section-strip"><div><b>优秀作文与本次小红花</b><span>发布结果后本班实名可见，不累计</span></div>
          <button class="btn small primary" id="showcase-save" ${assignment.lifecycleStatus === "archived" ? "disabled" : ""}>保存名单</button></div>
        ${eligible.length ? `<div class="showcase-picker">${eligible.map(item => `<label>
          <input type="checkbox" class="showcase-check" value="${h(item.id)}" ${selected.has(item.id) ? "checked" : ""}>
          <span><b>${h(item.studentName)}</b><small>${h(item.submittedBy)} · ${fmtScore(item.score)}</small></span>
        </label>`).join("")}</div>` : '<div class="empty compact-empty">暂无可选择的已完成作文</div>'}
      </section>
    </div>`;
}

export function buildStudentTrendChartConfig(assignments) {
  return {
    type: "line",
    data: {
      labels: assignments.map(item => `${formatLocalDateTime(item.date).slice(0, 10)} · ${item.label}`),
      datasets: [{
        label: "正式作业",
        data: assignments.map(item => item.score),
        borderColor: "#2563eb",
        backgroundColor: "#2563eb",
        pointRadius: 4,
        tension: 0.2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: 0, max: 100, ticks: { stepSize: 20 } },
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 6 } },
      },
    },
  };
}

export function renderInsightEditForm(currentInsight) {
  const insight = currentInsight?.insight || {};
  const fields = [
    ["overall", "整体表现"],
    ["commonStrengths", "共同优点"],
    ["commonProblems", "常见问题"],
    ["teachingSuggestions", "教学建议"],
  ];
  return `
    <div class="modal-title">编辑班级共性问题</div>
    ${fields.map(([key, label]) => `<label class="field"><span>${label}</span>
      <textarea class="input insight-field" data-key="${key}" rows="4" maxlength="3000">${h(insight[key] || "")}</textarea></label>`).join("")}
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn primary" id="insight-save">保存</button></div>`;
}
