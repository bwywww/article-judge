/* Authenticated API and async job helpers. */

export async function api(path, options = {}) {
  const headers = { "X-Token": localStorage.getItem("token") || "" };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  let data = null;
  try { data = await resp.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!resp.ok) {
    if (resp.status === 401 && path !== "/api/login") {
      localStorage.clear();
      location.href = "/";
      throw new Error("登录已过期");
    }
    const error = new Error((data && data.error) || `请求失败（${resp.status}）`);
    error.status = resp.status;
    error.code = data && data.code;
    throw error;
  }
  return data;
}


export async function downloadPdf(path) {
  const resp = await fetch(path, { headers: { "X-Token": localStorage.getItem("token") || "" } });
  if (!resp.ok) {
    let msg = `下载失败（${resp.status}）`;
    try { const d = await resp.json(); msg = d.error || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get("Content-Disposition") || "";
  let filename = "download.pdf";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const quoted = disposition.match(/filename="([^"]+)"/i);
  const plain = disposition.match(/filename=([^;]+)/i);
  try {
    if (encoded) filename = decodeURIComponent(encoded[1]);
    else if (quoted) filename = quoted[1];
    else if (plain) filename = plain[1].trim();
  } catch (error) { /* 保留安全默认文件名 */ }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename.replace(/[\\/:*?"<>|]/g, "_");
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

/* ---------- 批改结果渲染 ---------- */

export function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export async function pollStudentJob(jobId, onUpdate, interval = 1200) {
  const startedAt = Date.now();
  let delay = interval;
  while (true) {
    const job = await api(`/api/student/jobs/${encodeURIComponent(jobId)}`);
    if (onUpdate) await onUpdate(job);
    if (["done", "pending_review", "published", "error"].includes(job.status)) return job;
    if (Date.now() - startedAt >= 15 * 60 * 1000) {
      const error = new Error("前端已停止等待（超过 15 分钟），后台任务仍会继续；刷新页面可恢复状态");
      error.code = "poll_timeout";
      throw error;
    }
    await wait(delay);
    delay = Math.min(5000, Math.round(delay * 1.35));
  }
}
