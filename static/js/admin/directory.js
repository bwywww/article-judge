import { h } from "../shared/index.js";

export function permissionName(permission) {
  return permission === "admin" ? "管理员" : permission === "teacher" ? "老师" : "学生";
}

export function classChips(user) {
  return (user.classes || [])
    .map(klass => `<span class="chip blue">${h(klass.name)}</span>`)
    .join(" ") || "—";
}

export function studentLabel(user) {
  const number = user.studentNo || user.username;
  const klass = user.grade || user.klass
    ? ` · ${user.grade || ""}${user.klass || ""}`
    : "";
  return `${user.display_name || user.username} / ${number}${klass}`;
}
