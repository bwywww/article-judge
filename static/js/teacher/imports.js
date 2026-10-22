export function isPlaceholderName(name) {
  const value = String(name || "").trim();
  return !value || /^第\d+篇$/.test(value);
}

export function matchCandidateStudent(name, studentNoHint, students) {
  const nameKey = String(name || "").trim();
  const numberKey = String(studentNoHint || "").trim();
  const byName = students.filter(student => student.display_name === nameKey);
  if (byName.length === 1) return byName[0].username;
  const exact = numberKey
    ? students.find(student => [student.studentNo, student.username].includes(numberKey))
    : null;
  return exact ? exact.username : "";
}

export function candidateStudentLabel(username, students) {
  const student = students.find(item => item.username === username);
  if (!student) return "";
  const number = student.studentNo || student.username;
  const klass = student.grade || student.klass
    ? ` · ${student.grade || ""}${student.klass || ""}`
    : "";
  return `${student.display_name}（${number}）${klass}`;
}

export function duplicateCandidateUsernames(candidates) {
  const counts = {};
  candidates.forEach(candidate => {
    if (candidate.submittedBy) {
      counts[candidate.submittedBy] = (counts[candidate.submittedBy] || 0) + 1;
    }
  });
  return new Set(Object.keys(counts).filter(username => counts[username] > 1));
}

export function candidateVisible(candidate, duplicates, filter) {
  if (filter === "unbound") return !candidate.submittedBy;
  if (filter === "conflict") {
    return Boolean(candidate.submittedBy && duplicates.has(candidate.submittedBy));
  }
  return true;
}
