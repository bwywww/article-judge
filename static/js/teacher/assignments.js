import { formatLocalDateTime } from "../shared/index.js";

const LIFECYCLE_TEXT = {
  draft: "草稿",
  published: "已发布",
  closed: "已关闭",
  archived: "已归档",
};

const LIFECYCLE_CLASS = {
  draft: "gray",
  published: "green",
  closed: "orange",
  archived: "gray",
};

export function lifecycleChip(assignment) {
  const status = assignment.lifecycleStatus || "published";
  return `<span class="chip ${LIFECYCLE_CLASS[status] || "gray"}">${LIFECYCLE_TEXT[status] || status}</span>`;
}

export function lifecycleText(status) {
  return LIFECYCLE_TEXT[status] || status || "已发布";
}

export function deadlineText(assignment) {
  if (!assignment.deadlineAt) return "无截止时间";
  return `截止 ${formatLocalDateTime(assignment.deadlineAt)}`;
}
