/* Role-aware page initialization and feature visibility. */

import { api } from "./api.js";
import { openPasswordChangeModal } from "./account.js";
import { wireModal } from "./modal.js";
import { openSettings } from "./settings.js";

export async function pageInit(roles, callback) {
  wireModal();
  const menu = document.querySelector(".workspace-menu");
  const backdrop = document.querySelector(".workspace-backdrop");
  const setWorkspaceMenu = open => {
    document.body.classList.toggle("nav-open", open);
    if (menu) menu.setAttribute("aria-expanded", String(open));
  };
  const closeWorkspaceMenu = () => setWorkspaceMenu(false);
  if (menu) {
    menu.setAttribute("aria-expanded", "false");
    menu.addEventListener("click", () => setWorkspaceMenu(!document.body.classList.contains("nav-open")));
  }
  if (backdrop) backdrop.addEventListener("click", closeWorkspaceMenu);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && document.body.classList.contains("nav-open")) closeWorkspaceMenu();
  });
  document.querySelectorAll(".workspace-nav .nav-item").forEach(item => {
    item.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 1024px)").matches) closeWorkspaceMenu();
    });
  });
  try {
    const me = await api("/api/me");
    if (me.mustChangePassword) {
      await openPasswordChangeModal({ forced: true, onSuccess: () => location.reload() });
      return;
    }
    if (roles && !roles.includes(me.role)) {
      location.href = me.role === "admin" ? "/admin" : me.role === "teacher" ? "/teacher" : "/student/assignments";
      return;
    }
    const [cfg, featureData] = await Promise.all([
      api("/api/config"),
      api("/api/features"),
    ]);
    window.me = me;
    window.hasKey = cfg.has_key;
    window.features = featureData.features || {};
    applyFeatureVisibility();
    const nameEl = document.querySelector(".user-name");
    if (nameEl) nameEl.textContent = me.display_name || me.username;
    const logoutBtn = document.getElementById("btn-logout");
    if (logoutBtn) logoutBtn.addEventListener("click", async () => {
      try { await api("/api/logout", { method: "POST" }); } catch (e) { /* 本地仍需退出 */ }
      localStorage.clear();
      location.href = "/";
    });
    const setBtn = document.getElementById("btn-settings");
    if (setBtn) setBtn.addEventListener("click", openSettings);
    const keyBanner = document.getElementById("banner-key");
    if (keyBanner && me.role === "admin") keyBanner.addEventListener("click", openSettings);
    if (window.onConfigSaved === undefined) {
      window.onConfigSaved = () => {
        api("/api/config").then(c => { window.hasKey = c.has_key; refreshKeyBanner(); });
      };
    }
    refreshKeyBanner();
    if (callback) await callback(me, cfg);
  } catch (e) {
    if (e && e.status === 401) return;
    let panel = document.getElementById("page-init-error");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "page-init-error";
      panel.className = "card";
      panel.style.cssText = "position:fixed;inset:20% 20% auto;z-index:10000;padding:24px;text-align:center";
      document.body.appendChild(panel);
    }
    const message = String(e?.message || "暂时无法载入页面")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    panel.innerHTML = `<h2>页面初始化失败</h2><p>${message}</p>
      <button class="btn primary" id="page-init-retry">重新加载</button>`;
    document.getElementById("page-init-retry").addEventListener("click", () => location.reload());
  }
}

export function applyFeatureVisibility(root = document) {
  root.querySelectorAll("[data-feature]").forEach(element => {
    const feature = window.features && window.features[element.dataset.feature];
    element.hidden = Boolean(feature && feature.status === "disabled");
  });
  root.querySelectorAll("[data-feature-write]").forEach(element => {
    const feature = window.features && window.features[element.dataset.featureWrite];
    const available = !feature || feature.status === "enabled";
    element.hidden = !available;
    if ("disabled" in element) element.disabled = !available;
  });
}

export function refreshKeyBanner() {
  const el = document.getElementById("banner-key");
  if (!el) return;
  if (!window.hasKey) {
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}
