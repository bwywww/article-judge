export function aiTaskName(taskType) {
  const names = {
    grading: "作文批改",
    utility: "识别与结构化",
    upgrade: "整篇提升",
    class_insight: "班级共性问题",
    ocr: "扫描识别",
  };
  return names[taskType] || taskType;
}

export function numberText(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}
