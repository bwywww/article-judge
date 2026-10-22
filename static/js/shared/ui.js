/* Notifications and file drop interactions. */

export function toast(msg, type = "info", ms = 2800) {
  let box = document.getElementById("toast");
  if (!box) { box = document.createElement("div"); box.id = "toast"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = "toast-item" + (type === "error" ? " err" : type === "ok" ? " ok" : "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}


export function bindDropzone(dz, input, onChange) {
  if (!dz || !input) return;
  dz.addEventListener("click", () => input.click());
  dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", e => {
    e.preventDefault(); dz.classList.remove("drag");
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; onChange(input.files[0]); }
  });
  input.addEventListener("change", () => { if (input.files.length) onChange(input.files[0]); });
}

/* ---------- 页面初始化 ---------- */
