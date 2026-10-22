"""Shared operational settings and AI quota checks."""

import datetime

import database as db


SCHOOL_SETTING_KEYS = (
    "school_name", "school_academic_year_note", "ai_daily_token_limit",
    "ai_monthly_token_limit",
)


def ai_quota_status():
    settings = db.get_app_settings(list(SCHOOL_SETTING_KEYS)) if db.is_ready() else {}
    try:
        daily_limit = max(0, int(settings.get("ai_daily_token_limit") or 0))
        monthly_limit = max(0, int(settings.get("ai_monthly_token_limit") or 0))
    except ValueError:
        daily_limit = monthly_limit = 0
    china = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(china)
    day_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_local = day_local.replace(day=1)
    totals = db.ai_token_totals(
        day_local.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        month_local.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    ) if db.is_ready() else {"dailyTokens": 0, "monthlyTokens": 0}
    blocked = ((daily_limit and totals["dailyTokens"] >= daily_limit)
               or (monthly_limit and totals["monthlyTokens"] >= monthly_limit))
    warning = ((daily_limit and totals["dailyTokens"] >= daily_limit * 0.8)
               or (monthly_limit and totals["monthlyTokens"] >= monthly_limit * 0.8))
    return {**totals, "dailyLimit": daily_limit, "monthlyLimit": monthly_limit,
            "blocked": bool(blocked), "warning": bool(warning)}


def ai_quota_error():
    status = ai_quota_status()
    if not status["blocked"]:
        return None
    return {
        "error": "AI 用量已达到管理员设置的限额，新任务暂时暂停；已排队任务会继续完成",
        "code": "ai_quota_exceeded", "quota": status,
    }, 503
