/* Escaping, dates, scores and feedback rendering. */

export function h(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function fmtScore(score) {
  if (score == null) return '<span class="score none">—</span>';
  const cls = score >= 90 ? "high" : score >= 80 ? "mid" : score >= 70 ? "low" : "bad";
  return `<span class="score ${cls}">${score}</span>`;
}

export function formatLocalDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function toDateTimeLocalValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = number => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function dateTimeLocalToUtc(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("截止时间格式无效");
  return date.toISOString();
}

/* ---------- 弹窗 ---------- */

export function secretSourceName(source) {
  const names = {
    environment: "服务器环境变量",
    legacy_environment: "兼容环境变量",
    file: "本地开发文件",
  };
  return names[source] || "未配置";
}

export function renderAiProfileStatus(profiles = {}) {
  const profileNames = { article_judge: "作文业务", ecnu_plus: "OCR 识别" };
  // 遍历传入的 profiles，未知名使用原始 key；code_develop 等开发专用不在此展示。
  const keys = Object.keys(profiles).filter(k => profileNames[k]);
  return keys.map(key => {
    const profile = profiles[key] || {};
    const name = profileNames[key] || key;
    const statusClass = profile.configured ? "green" : "orange";
    const statusText = profile.configured ? "可用" : "未配置";
    return `<div class="config-status-row">
      <div><b>${name}</b><small>${key}</small></div>
      <span class="chip ${statusClass}">${statusText}</span>
      <span>托管：${h(secretSourceName(profile.source))}</span>
    </div>`;
  }).join("");
}

/* ---------- 设置弹窗（连接参数，不接收密钥） ---------- */

export function renderFeedback(el, fb) {
  const dims = fb.dimensions || {};
  const dimRows = ["立意与内容", "结构与逻辑", "语言表达"].map(k => {
    const v = dims[k];
    if (v == null) return "";
    const width = Math.max(4, Math.min(100, v));
    return `<div class="dim-row">
      <div class="dim-name">${k}</div>
      <div class="dim-bar"><i style="width:${width}%"></i></div>
      <div class="dim-val">${v}</div>
    </div>`;
  }).join("");
  const sec = (cls, title, items) => items && items.length ? `
    <div class="fb-sec ${cls}"><h4>${title}</h4><ul>
      ${items.map(i => `<li>${h(i)}</li>`).join("")}
    </ul></div>` : "";
  el.innerHTML = `
    <div class="fb-head">
      <div class="fb-score" style="--fc:${scoreColor(fb.score)}">
        <div class="num">${fb.score}</div><div class="unit">分</div>
      </div>
      <div>
        <div class="fb-level" style="color:${scoreColor(fb.score)}">${h(fb.level || "")}</div>
        ${fb.gradedAt ? `<div style="color:var(--text-2);font-size:13px">批改时间：${h(fb.gradedAt)}</div>` : ""}
      </div>
    </div>
    ${fb.summary ? `<div class="fb-summary">${h(fb.summary)}</div>` : ""}
    <div style="margin-top:14px">${dimRows}</div>
    ${sec("good", "优点", fb.strengths)}
    ${sec("bad", "不足与问题", fb.weaknesses)}
    ${sec("fix", "具体修改意见", fb.suggestions)}
    ${fb.modelSnippet ? `<div class="fb-sec demo"><h4>好句示范</h4><div class="demo-box">${h(fb.modelSnippet)}</div></div>` : ""}
    ${fb.upgrade ? `<details class="essay-text" style="margin-top:8px"><summary>AI 润色提升版</summary>
      <div class="essay-preview" style="color:var(--text-1)">${h(fb.upgrade)}</div></details>` : ""}`;
}

export function scoreColor(score) {
  if (score == null) return "#64748b";
  if (score >= 90) return "#16a34a";
  if (score >= 80) return "#0891b2";
  if (score >= 70) return "#d97706";
  return "#dc2626";
}
