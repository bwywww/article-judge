import { formatLocalDateTime, h } from "../shared/index.js";

export function renderReturnSubmissionForm(essay) {
  return `
    <div class="modal-title">退回 ${h(essay.studentName)} 的作文</div>
    <div class="banner-key plain">只为该学生开放重交，不会重新开放全班作业。旧稿和已发布结果会保留。</div>
    <label class="field"><span>退回评语 <span class="req">*</span></span>
      <textarea class="input" id="return-comment" rows="6" maxlength="1000" placeholder="说明需要修改的内容和重交要求"></textarea></label>
    <label class="field"><span>重交截止时间（可留空）</span>
      <input class="input" id="return-deadline" type="datetime-local"></label>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">取消</button>
      <button class="btn warning" id="return-confirm">确认退回</button></div>`;
}

export function renderRevisionTimeline(timeline) {
  const returns = new Map((timeline.returns || []).map(item => [item.versionId, item]));
  return `
    <div class="modal-title">${h(timeline.studentName)} · 作文版本</div>
    <div class="revision-timeline">${(timeline.versions || []).map(version => {
      const returned = returns.get(version.id);
      return `<article class="revision-entry"><div class="revision-marker"></div><div>
        <h3>第 ${version.versionNo} 稿 <span class="chip gray">${h(version.status)}</span></h3>
        <p>${h(formatLocalDateTime(version.submittedAt))}${version.score != null ? ` · ${version.score} 分` : ""}</p>
        <details><summary>查看正文</summary><div class="essay-preview">${h(version.text)}</div></details>
        ${returned ? `<div class="return-note"><b>退回评语</b><p>${h(returned.comment)}</p>
          <small>${returned.resubmitDeadlineAt ? `重交截止 ${h(formatLocalDateTime(returned.resubmitDeadlineAt))}` : "未设置重交截止"}</small>
          ${!returned.resolvedAt && !returned.cancelledAt ? '<button class="btn small danger cancel-return">撤销退回</button>' : ""}
          ${returned.cancelledAt ? '<span class="chip gray">已撤销</span>' : ""}</div>` : ""}
      </div></article>`;
    }).join("")}</div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">关闭</button></div>`;
}
