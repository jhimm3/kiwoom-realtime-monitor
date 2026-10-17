"""Human-facing KST rendering; never change persisted timestamps or identities."""
from datetime import date, datetime
from kiwoom_monitor.domain.time_basis import KST


def format_kst_time(value: object, pattern: str = "%Y-%m-%d %H:%M:%S KST", *, missing: str = "-") -> str:
    if value is None or value == "":
        return missing
    try:
        if isinstance(value, datetime):
            stamp = value
        elif type(value) in (int, float):
            stamp = datetime.fromtimestamp(value, KST)
        elif isinstance(value, date):
            return value.isoformat()
        else:
            raw = str(value)
            # A date-only source has no known time. Do not invent midnight.
            if len(raw) == 10:
                return date.fromisoformat(raw).isoformat()
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # Existing offset-free broker/journal values denote Korean wall time.
        # Offset-aware values and epochs always preserve the original instant.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        return stamp.astimezone(KST).strftime(pattern)
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)
