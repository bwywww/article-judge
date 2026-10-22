import { h } from "../shared/index.js";

function pageRows(rows, requestedPage, pageSize = 50) {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(Math.max(1, requestedPage), pageCount);
  return {
    page,
    pageCount,
    pageSize,
    visible: rows.slice((page - 1) * pageSize, page * pageSize),
  };
}

export function teacherImportPreviewView(preview, requestedPage = 1) {
  const summary = preview.summary || {};
  const rows = preview.rows || [];
  const paging = pageRows(rows, requestedPage);
  const html = `
    <div class="modal-title">老师导入预览</div>
    <div class="import-summary"><span class="chip gray">总计 ${summary.total || 0}</span>
      <span class="chip green">新增 ${summary.new || 0}</span><span class="chip orange">更新 ${summary.update || 0}</span>
      <span class="chip red">错误 ${summary.error || 0}</span></div>
    <div class="picker-context">工作表“${h(summary.sheetName || "-")}”，表头第 ${h(summary.headerRow || "-")} 行。已有工号会整行更新姓名、学科和完整班级集合，密码保持不变。</div>
    <div class="table-wrap import-preview-scroll"><table class="table responsive-table">
      <thead><tr><th>行</th><th>姓名</th><th>工号</th><th>学科</th><th>任教班级</th><th>状态</th><th>提示</th></tr></thead>
      <tbody>${paging.visible.map(row => `<tr><td data-label="行">${h(row.rowNo)}</td>
        <td data-label="姓名"><b>${h(row.display_name)}</b></td><td data-label="工号">${h(row.employeeNo)}</td>
        <td data-label="学科">${h(row.subject)}</td><td data-label="任教班级">${(row.classNames || []).map(name => `<span class="chip blue">${h(name)}</span>`).join(" ") || "—"}</td>
        <td data-label="状态"><span class="chip ${row.status === "error" ? "red" : row.status === "update" ? "orange" : "green"}">${row.status === "error" ? "错误" : row.status === "update" ? "更新" : "新增"}</span></td>
        <td data-label="提示">${(row.errors || []).map(h).join("；") || "—"}</td></tr>`).join("")}</tbody>
    </table></div>
    <div class="pagination"><button class="btn ghost" id="teacher-import-prev" ${paging.page <= 1 ? "disabled" : ""}>上一页</button>
      <span>第 ${paging.page} / ${paging.pageCount} 页</span><button class="btn ghost" id="teacher-import-next" ${paging.page >= paging.pageCount ? "disabled" : ""}>下一页</button></div>
    <div class="modal-actions"><button class="btn ghost" id="teacher-import-cancel">取消</button>
      <button class="btn primary" id="teacher-import-confirm" ${summary.error ? "disabled" : ""}>确认导入</button></div>`;
  return { ...paging, html, rows, summary };
}

export function studentImportPreviewView(preview, requestedPage = 1) {
  const summary = preview.summary || {};
  const rows = preview.rows || [];
  const paging = pageRows(rows, requestedPage);
  const statusText = row => row.status === "new" ? "新增" : row.status === "update" ? "更新" : "错误";
  const statusClass = row => row.status === "error" ? "red" : row.status === "update" ? "orange" : "green";
  const aiReview = preview.aiReview || {};
  const html = `
    <div class="modal-title">导入预览</div>
    <div class="import-summary">
      <span class="chip gray">总计 ${summary.total || 0}</span>
      <span class="chip green">新增 ${summary.new || 0}</span>
      <span class="chip orange">更新 ${summary.update || 0}</span>
      <span class="chip red">错误 ${summary.error || 0}</span>
      <span class="chip blue">将创建班级 ${summary.createClasses || 0}</span>
      <span class="chip gray">自动生成学号 ${summary.generatedStudentNos || 0}</span>
    </div>
    <div class="picker-context">
      已读取工作表“${h(summary.sheetName || "-")}”（表头第 ${h(summary.headerRow || "-")} 行），按 ${h(summary.academicYearStart || "-")} 学年计算入学年份。
      ${summary.normalizedClassRows ? `已规范化 ${h(summary.normalizedClassRows)} 行组合班级名称。` : ""}
    </div>
    <div class="picker-context">
      <b>本地规则校验：</b>${h(aiReview.summary || "仅执行本地规则校验；未向外部 AI 发送学生名单。")}
    </div>
    ${(preview.classesToCreate || []).length ? `<details class="import-class-review">
      <summary>查看将创建的 ${preview.classesToCreate.length} 个班级</summary>
      <div class="import-class-chips">${preview.classesToCreate.map(klass => `<span class="chip blue">${h(klass.name)}</span>`).join(" ")}</div>
    </details>` : ""}
    <div class="import-preview-scroll">
      <table class="table responsive-table import-preview-table">
        <thead><tr><th>行</th><th>姓名</th><th>学号</th><th>年级班级</th><th>状态</th><th>提示</th></tr></thead>
        <tbody>${paging.visible.map(row => `
          <tr>
            <td data-label="行号">${h(row.rowNo)}</td>
            <td data-label="姓名"><b>${h(row.display_name)}</b></td>
            <td data-label="学号"><div><b>${h(row.studentNo)}</b>${row.studentNoGenerated ? ' <span class="chip gray">自动生成</span>' : ""}
              ${row.studentNoGenerated ? `<div class="student-number-meta">入学 ${h(row.admissionYear)} · 班内 ${h(row.classStudentNo)}</div>` : ""}</div></td>
            <td data-label="年级 / 班级">${h(row.grade)} / ${h(row.klass)}</td>
            <td data-label="处理方式"><span class="chip ${statusClass(row)}">${statusText(row)}</span></td>
            <td data-label="审核结果" class="import-review-result">${[...(row.errors || []), ...(row.warnings || [])].map(h).join("；") || "—"}</td>
          </tr>`).join("")}</tbody>
      </table>
    </div>
    <div class="pagination import-preview-pagination">
      <button class="btn ghost" id="import-page-prev" ${paging.page <= 1 ? "disabled" : ""}>上一页</button>
      <span>第 ${paging.page} / ${paging.pageCount} 页 · 每页 ${paging.pageSize} 行</span>
      <button class="btn ghost" id="import-page-next" ${paging.page >= paging.pageCount ? "disabled" : ""}>下一页</button>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" id="import-cancel-btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="import-confirm-btn" ${summary.error ? "disabled" : ""}>确认导入</button>
    </div>`;
  return { ...paging, html, rows, summary };
}
