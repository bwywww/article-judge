export function moduleStatusName(status) {
  return status === "enabled" ? "已启用" : status === "draining" ? "排空中" : "已停用";
}

export function moduleStatusClass(status) {
  return status === "enabled" ? "green" : status === "draining" ? "orange" : "gray";
}
