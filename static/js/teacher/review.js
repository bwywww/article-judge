export function feedbackListText(value) {
  return Array.isArray(value) ? value.join("\n") : (value || "");
}

export function defaultFeedbackGrade(score) {
  const value = Number(score || 0);
  return value >= 85 ? "优" : value >= 70 ? "良" : value >= 60 ? "中" : "差";
}
