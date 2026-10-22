import { fmtScore, h } from "../shared/index.js";

export const STATUS_CHIP = {
  pending: '<span class="chip gray">待批改</span>',
  queued: '<span class="chip gray">排队中</span>',
  grading: '<span class="chip blue">批改中…</span>',
  done: '<span class="chip green">已完成</span>',
  error: '<span class="chip red">失败</span>',
};

export function renderGradingProgress(essays, finishedBase, startedAt) {
  const total = essays.length;
  const finished = essays.filter(essay => ["done", "error"].includes(essay.status)).length;
  const percent = total ? Math.round(finished / total * 100) : 0;
  const completedNow = finished - finishedBase;
  const elapsedSeconds = startedAt ? (Date.now() - startedAt) / 1000 : 0;
  let remaining = "约 1-2 分钟";
  if (completedNow > 0 && elapsedSeconds > 3) {
    const perSecond = completedNow / elapsedSeconds;
    const remainingSeconds = Math.max(0, (total - finished) / perSecond);
    remaining = remainingSeconds <= 60
      ? `约 ${Math.max(10, Math.round(remainingSeconds / 10) * 10)} 秒`
      : `约 ${Math.ceil(remainingSeconds / 60)} 分钟`;
  }
  const chips = essays.map(essay => `
    <span style="display:inline-flex;align-items:center;gap:6px;margin:0 10px 8px 0">
      ${h(essay.studentName)} ${STATUS_CHIP[essay.status] || ""}
      ${essay.attempt ? `<span class="job-meta">尝试 ${essay.attempt}</span>` : ""}
      ${essay.status === "error" ? `<span style="color:var(--danger);font-size:11px" title="${h(essay.errorAdvice || essay.error || "")}">${h(essay.errorType || (essay.error || "").slice(0, 18))}</span>` : ""}
    </span>`).join("");
  return `
    <div class="card progress-card">
      <div class="section-title" style="margin:0 0 10px">
        <h3>AI 批量批改中…</h3>
        <span class="chip blue">正在为 ${total} 篇作文逐一评阅</span>
      </div>
      <div class="progress"><div style="width:${percent}%"></div></div>
      <div class="progress-info">
        <span>已完成 ${finished} / ${total} 篇</span>
        <span>预计还需${remaining}</span>
      </div>
      <div style="margin-top:12px;border-top:1px dashed var(--border);padding-top:10px">${chips}</div>
    </div>`;
}

export function renderResultsTable(essays, ranking) {
  const done = essays.filter(essay => essay.status === "done");
  const pendingReview = done.filter(essay => essay.resultStatus === "pending_review");
  const rankingRows = ranking.length
    ? ranking.map(rank => ({ rank, essay: essays.find(item => item.id === rank.essayId) || null }))
      .concat(essays.filter(item => !item.submittedBy).map(essay => ({ rank: null, essay })))
    : essays.slice()
      .sort((a, b) => (b.score ?? -1) - (a.score ?? -1)
        || String(a.submittedBy).localeCompare(String(b.submittedBy)))
      .map((essay, index) => ({ rank: essay.score == null ? null : { rank: index + 1 }, essay }));
  const rows = rankingRows.map(({ rank, essay }) => essay ? `
    <tr>
      <td data-label="名次">${rank?.rank ? `<b>${rank.rank}</b>` : "—"}</td>
      <td data-label="学生"><b>${h(essay.studentName)}</b></td>
      <td data-label="作文标题">${h(essay.studentTitle || "—")}</td>
      <td data-label="提交方式">${essay.submissionType === "student_upload" ? '<span class="chip blue">学生提交</span>' : '<span class="chip gray">老师导入</span>'}
        ${essay.submissionOpen ? '<span class="chip orange">已开放重交</span>' : ""}</td>
      <td data-label="分数 / 状态">${essay.status === "done" ? fmtScore(essay.score) : STATUS_CHIP[essay.status]}</td>
      <td data-label="结果状态">${essay.status !== "done" ? "—" : essay.resultStatus === "published"
        ? '<span class="chip green">已发布</span>' : '<span class="chip orange">待审核发布</span>'}</td>
      <td data-label="等级">${h(essay.level || "—")}</td>
      <td data-label="批改时间" style="color:var(--text-2);font-size:12.5px">${h(essay.gradedAt || "—")}${essay.durationSeconds != null ? `<br>${h(essay.durationSeconds)} 秒` : ""}</td>
      <td class="row-actions" data-label="操作">
        ${essay.status === "done" ? `
          <button class="btn small ghost view" data-id="${essay.id}">详情</button>
          ${essay.resultStatus === "pending_review" ? `<button class="btn small primary publish-result" data-id="${essay.id}">发布结果</button>` : ""}
          <button class="btn small ghost regrade" data-id="${essay.id}">重新批改</button>
          <button class="btn small ghost export" data-id="${essay.id}">导出 PDF</button>
          ${essay.submittedBy ? `<button class="btn small ghost versions" data-id="${essay.id}">版本</button>
            <button class="btn small ghost student-trend" data-username="${h(essay.submittedBy)}">趋势</button>` : ""}
          ${essay.submissionType === "student_upload" && essay.submittedBy && !essay.submissionOpen
            ? `<button class="btn small warning return-submission" data-id="${essay.id}">退回重交</button>` : ""}`
        : essay.status === "error" ? `
          <button class="btn small ghost regrade" data-id="${essay.id}">重试</button>
          ${essay.submittedBy ? `<button class="btn small ghost versions" data-id="${essay.id}">版本</button>` : ""}
          <span style="color:var(--danger);font-size:12px" title="${h((essay.errorAdvice || "") + (essay.error ? ` ${essay.error}` : ""))}">
            ${h(essay.errorType || (essay.error || "").slice(0, 26))}
          </span>` : essay.submittedBy ? `<button class="btn small ghost versions" data-id="${essay.id}">版本</button>` : ""}
      </td>
    </tr>` : `<tr><td data-label="名次">—</td><td data-label="学生"><b>${h(rank.displayName)}</b><small class="table-subtext">${h(rank.studentNo)}</small></td><td data-label="作文标题">—</td>
      <td data-label="提交方式">未提交</td><td data-label="分数 / 状态">—</td><td data-label="结果状态">—</td><td data-label="等级">—</td><td data-label="批改时间">—</td>
      <td data-label="操作"><button class="btn small ghost student-trend" data-username="${h(rank.username)}">历次趋势</button></td></tr>`).join("");
  const average = done.length
    ? (done.reduce((sum, essay) => sum + (essay.score || 0), 0) / done.length).toFixed(1)
    : null;
  return `
    <div class="section-title">
      <h3>批改结果（${done.length}/${essays.length}）</h3>
      <div class="ops">
        ${average != null ? `<span class="chip green">平均分 ${average}</span>` : ""}
        ${essays.some(essay => essay.status === "pending") ? '<button class="btn small primary" id="bt-grade">开始批改</button>' : ""}
        ${pendingReview.length ? `<button class="btn small primary" id="bt-publish-results">批量发布 ${pendingReview.length} 篇</button>` : ""}
        ${done.length ? `<button class="btn small" id="bt-regrade-all">全部重新批改</button>
        <button class="btn small" id="bt-completion">导出完成情况 XLSX</button>
        <button class="btn small primary" id="bt-export">导出全班批改报告 PDF</button>` : ""}
      </div>
    </div>
    <div class="data-surface table-wrap">
      <table class="table responsive-table">
        <thead><tr><th>名次</th><th>学生</th><th>作文标题</th><th>提交方式</th><th>分数</th><th>结果状态</th><th>等级</th><th>批改时间</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
