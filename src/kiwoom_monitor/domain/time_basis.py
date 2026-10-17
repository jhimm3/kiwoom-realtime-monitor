"""Application wall-time representation; broker clocks remain caller-owned."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def as_kst(value: datetime) -> datetime:
    """Offset-free application/broker wall times already denote Korea time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def kst_now() -> datetime:
    """Explicit local fallback when no broker/application clock is supplied."""
    return datetime.now(KST)


def kst_wall_now() -> datetime:
    """Korean wall time for existing offset-free UI/journal interfaces."""
    return kst_now().replace(tzinfo=None)


def kst_today():
    return kst_now().date()


def aware_iso_kst(value: str) -> datetime:
    """Parse an explicit instant. Unlabelled storage values need an owner policy."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include its timezone")
    return parsed.astimezone(KST)
