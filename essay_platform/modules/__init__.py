"""Business Blueprint registry."""

from . import (
    academic_years, analytics, assignments, auth, directory, grading, imports,
    operations, pages, reporting, review, revisions, showcase, uploads, users,
)


MODULE_PACKAGES = (
    auth, directory, users, imports, uploads, assignments, grading, review,
    revisions, analytics, showcase, academic_years, reporting, operations,
    pages,
)


def build_blueprints():
    return [module.build_blueprint() for module in MODULE_PACKAGES]


def manifests():
    return [module.MANIFEST for module in MODULE_PACKAGES]
