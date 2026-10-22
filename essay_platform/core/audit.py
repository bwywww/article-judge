"""Audit context and persistence helpers."""

from flask import has_request_context, request

import database as db

def client_ip():
    return (request.remote_addr or "")[:128] if has_request_context() else ""


def audit_event(event_type, *, actor="", target_type="", target_id="", detail=None):
    if not db.is_ready():
        return
    db.write_audit(
        event_type,
        actor_username=actor or (getattr(request, "user", {}) or {}).get("username")
        if has_request_context() else actor or None,
        target_type=target_type,
        target_id=target_id,
        ip_address=client_ip(),
        detail=detail or {},
    )
