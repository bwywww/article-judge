"""Time, deadline and content hash helpers."""

import datetime
import hashlib

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def utc_iso(value=None):
    current = value or utc_now()
    return current.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_deadline(value, *, allow_empty=True):
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("请设置截止时间")
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("截止时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return utc_iso(parsed)


def deadline_is_future(deadline_at):
    if not deadline_at:
        return True
    parsed = datetime.datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    return parsed > utc_now()


def content_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
