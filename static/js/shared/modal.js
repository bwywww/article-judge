/* Accessible modal lifecycle and focus management. */

let modalReturnFocus = null;
let modalDismissible = true;
let modalPointerStartedOnBackdrop = false;
export function openModal(html, options = {}) {
  const m = document.getElementById("modal");
  modalDismissible = options.dismissible !== false;
  modalPointerStartedOnBackdrop = false;
  modalReturnFocus = document.activeElement;
  document.getElementById("modal-box").innerHTML = html;
  m.hidden = false;
  requestAnimationFrame(() => {
    const first = m.querySelector("input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled])");
    if (first) first.focus();
  });
}
export function closeModal(force = false) {
  if (!modalDismissible && !force) return;
  document.getElementById("modal").hidden = true;
  modalPointerStartedOnBackdrop = false;
  if (modalReturnFocus && modalReturnFocus.focus) modalReturnFocus.focus();
  modalReturnFocus = null;
}
export function wireModal() {
  const m = document.getElementById("modal");
  if (m.dataset.wired === "true") return;
  m.dataset.wired = "true";
  m.addEventListener("pointerdown", e => {
    modalPointerStartedOnBackdrop = e.target === m;
  });
  m.addEventListener("pointercancel", () => { modalPointerStartedOnBackdrop = false; });
  m.addEventListener("click", e => {
    if (e.target === m && modalPointerStartedOnBackdrop && modalDismissible) closeModal();
    modalPointerStartedOnBackdrop = false;
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !m.hidden && modalDismissible) closeModal();
    if (e.key !== "Tab" || m.hidden) return;
    const focusable = [...m.querySelectorAll("input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), a[href]")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

export function setModalDismissible(value) {
  modalDismissible = Boolean(value);
}

window.closeModal = closeModal;
