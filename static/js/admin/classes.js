import { h } from "../shared/index.js";

export function renderClassPanel(classes) {
  return `
    <div class="page-heading compact-heading">
      <div><h1>班级管理</h1><p>维护行政班，并分别分配老师和学生。</p></div>
      <button class="btn primary" id="bt-new-class">新建班级</button>
    </div>
    <section class="data-surface">
      <div class="data-toolbar"><span class="data-count">共 ${classes.length} 个班级</span></div>
      <div class="table-wrap"><table class="table responsive-table">
        <thead><tr><th>班级名称</th><th>年级</th><th>班级</th><th>老师</th><th>学生</th><th>操作</th></tr></thead>
        <tbody>${classes.map(klass => `<tr>
          <td data-label="班级名称"><b>${h(klass.name)}</b></td><td data-label="年级">${h(klass.grade || "—")}</td><td data-label="班级">${h(klass.klass || "—")}</td>
          <td data-label="老师"><button class="btn small ghost view-members" data-id="${h(klass.id)}" data-role="teacher">${klass.teacherCount || 0} 位</button></td>
          <td data-label="学生"><button class="btn small ghost view-members" data-id="${h(klass.id)}" data-role="student">${klass.studentCount || 0} 名</button></td>
          <td class="row-actions" data-label="操作">
            <button class="btn small add-teacher" data-id="${h(klass.id)}">加入老师</button>
            <button class="btn small add-student" data-id="${h(klass.id)}">加入学生</button>
            <button class="btn small ghost class-edit" data-id="${h(klass.id)}">编辑</button>
            <button class="btn small danger class-del" data-id="${h(klass.id)}">删除</button>
          </td></tr>`).join("") || '<tr><td colspan="6" class="empty compact-empty">还没有班级</td></tr>'}</tbody>
      </table></div>
    </section>`;
}
