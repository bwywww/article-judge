"""Modular application package for the essay grading platform.

Keep the application factory lazy: the top-level ``database`` compatibility
facade imports persistence modules, and eagerly importing all HTTP modules here
would create a circular import for maintenance and migration tools.
"""


def create_app(*args, **kwargs):
    from .application import create_app as factory
    return factory(*args, **kwargs)


__all__ = ["create_app"]
