/* Password change workflow. */

import { api } from "./api.js";
import { h } from "./format.js";
import { closeModal, openModal } from "./modal.js";
import { toast } from "./ui.js";

export async function openPasswordChangeModal(options = {}) {
  const forced = options.forced !== false;
  const currentPassword = options.currentPassword || "";
  openModal(`
    <form id="pwd-form">
      <div class="modal-title">${forced ? "首次登录，请修改密码" : "修改密码"}</div>
      <div class="form-note">新密码至少 10 位。修改成功后，其他已登录会话将全部退出。</div>
      <label class="field"><span>当前密码</span>
        <input class="input" id="pwd-current" type="password" autocomplete="current-password" value="${h(currentPassword)}" required></label>
      <label class="field"><span>新密码</span>
        <input class="input" id="pwd-new" type="password" autocomplete="new-password" minlength="10" required></label>
      <label class="field"><span>确认新密码</span>
        <input class="input" id="pwd-confirm" type="password" autocomplete="new-password" minlength="10" required></label>
      <div class="modal-actions">
        ${forced ? "" : `<button class="btn ghost" id="pwd-cancel" type="button">取消</button>`}
        <button class="btn primary" id="pwd-save" type="submit">保存新密码</button>
      </div>
    </form>`, { dismissible: !forced });
  if (!forced) document.getElementById("pwd-cancel").addEventListener("click", () => closeModal());
  const passwordForm = document.getElementById("pwd-form");
  passwordForm.addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    passwordForm.requestSubmit();
  });
  passwordForm.addEventListener("submit", async event => {
    event.preventDefault();
    const button = document.getElementById("pwd-save");
    if (button.disabled) return;
    const current = document.getElementById("pwd-current").value;
    const next = document.getElementById("pwd-new").value;
    const confirm = document.getElementById("pwd-confirm").value;
    if (next.length < 10) { toast("新密码至少需要 10 位", "error"); return; }
    if (next !== confirm) { toast("两次输入的新密码不一致", "error"); return; }
    button.disabled = true;
    try {
      const result = await api("/api/account/password", {
        method: "POST",
        body: JSON.stringify({ currentPassword: current, newPassword: next }),
      });
      localStorage.setItem("token", result.token);
      closeModal(true);
      toast("密码已更新", "ok");
      if (options.onSuccess) await options.onSuccess(result);
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  });
}
