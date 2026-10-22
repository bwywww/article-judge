"""Helpers shared by the platform's Flask Blueprints."""

from __future__ import annotations

from dataclasses import dataclass

from collections.abc import Mapping

from flask import Blueprint

from essay_platform.core.modules import feature_required


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    view: str
    write: bool | None = None
    endpoint: str = ""
    feature: str | None = None


def create_blueprint(name: str, import_name: str, routes: tuple[RouteSpec, ...],
                     *, default_feature: str | None = None,
                     namespace: Mapping[str, object]) -> Blueprint:
    blueprint = Blueprint(name, import_name)
    for spec in routes:
        view = namespace[spec.view]
        feature = spec.feature if spec.feature is not None else default_feature
        if feature:
            write = spec.write if spec.write is not None else spec.method.upper() != "GET"
            view = feature_required(feature, write=write)(view)
        blueprint.add_url_rule(
            spec.path,
            endpoint=spec.endpoint or spec.view,
            view_func=view,
            methods=[spec.method.upper()],
        )
    return blueprint
