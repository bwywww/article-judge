/* Administrator-only AI connection settings. */

import { api } from "./api.js";
import { h, renderAiProfileStatus } from "./format.js";
import { closeModal, openModal } from "./modal.js";
import { toast } from "./ui.js";

export async function openSettings() {
  const cfg = await api("/api/config");
  const canEdit = localStorage.getItem("role") === "admin";
  openModal(`
    <div class="modal-title">AI 接口设置</div>
    ${canEdit ? "" : `<div class="chip orange" style="margin-bottom:12px">AI 接口由管理员统一配置</div>`}
    <div class="config-status-list">${renderAiProfileStatus(cfg.profiles)}</div>
    <div class="form-note">密钥只通过服务器环境变量托管；开发环境可使用已忽略的本地密钥文件。网页不会读取、保存或显示密钥。</div>
    <label class="field"><span>接口地址 Base URL</span>
      <input class="input" id="cfg-base" value="${h(cfg.base_url)}" placeholder="https://api.deepseek.com" ${canEdit ? "" : "disabled"}></label>
    <div class="form-note">仅允许 https://api.deepseek.com 或 https://api.deepseek.com/v1。</div>
    <label class="field"><span>模型</span>
      <input class="input" id="cfg-model" value="${h(cfg.model)}" placeholder="deepseek-v4-flash" ${canEdit ? "" : "disabled"}></label>
    <div class="modal-actions">
      <button class="btn ghost" id="cfg-test" ${canEdit ? "" : "disabled"}>测试连接</button>
      <button class="btn ghost" id="cfg-cancel">取消</button>
      <button class="btn primary" id="cfg-save" ${canEdit ? "" : "disabled"}>保存</button>
    </div>`);
  const btn = (id, cb) => document.getElementById(id).addEventListener("click", cb);
  btn("cfg-cancel", () => closeModal());
  btn("cfg-test", async () => {
    const t = document.getElementById("cfg-test");
    t.disabled = true; t.textContent = "测试中…";
    try {
      const r = await api("/api/config/test", { method: "POST", body: JSON.stringify({
        base_url: document.getElementById("cfg-base").value.trim(),
        model: document.getElementById("cfg-model").value.trim(),
      }) });
      toast(`连接成功，当前模型：${r.model}`, "ok");
    } catch (e) {
      toast(e.message, "error", 4500);
    } finally { t.disabled = false; t.textContent = "测试连接"; }
  });
  btn("cfg-save", async () => {
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify({
        base_url: document.getElementById("cfg-base").value.trim(),
        model: document.getElementById("cfg-model").value.trim(),
      }) });
      toast("设置已保存", "ok");
      closeModal();
      if (window.onConfigSaved) window.onConfigSaved();
    } catch (e) { toast(e.message, "error"); }
  });
}

/* ---------- PDF 下载 ---------- */
