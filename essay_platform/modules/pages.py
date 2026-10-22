"""Server-rendered role workspaces."""

from .base import RouteSpec, create_blueprint
from flask import redirect, render_template

MANIFEST = {
    "key": "pages", "dependencies": [], "permissions": ["public"],
    "navigation": [], "tasks": [], "migrations": [],
}

ROUTES = (
    RouteSpec("GET", "/", "page_login"),
    RouteSpec("GET", "/teacher", "page_teacher"),
    RouteSpec("GET", "/admin", "page_admin"),
    RouteSpec("GET", "/student", "page_student"),
    RouteSpec("GET", "/student/assignments", "page_student_assignments", feature="assignments"),
    RouteSpec("GET", "/student/practice", "page_student_practice", feature="practice"),
    RouteSpec("GET", "/student/history", "page_student_history", feature="reporting"),
)



def page_login():
    return render_template("login.html")


def page_teacher():
    return render_template("teacher.html")


def page_admin():
    return render_template("admin.html")


def page_student():
    return redirect("/student/assignments")


def page_student_assignments():
    return render_template("student.html")


def page_student_practice():
    return render_template("student_practice.html")


def page_student_history():
    return render_template("student_history.html")

def build_blueprint():
    return create_blueprint("pages", __name__, ROUTES, namespace=globals())
