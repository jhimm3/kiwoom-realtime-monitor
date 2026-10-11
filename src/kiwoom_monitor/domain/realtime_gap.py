"""Bounded subscriber loss scope shared by the hub and desktop consumers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def market_clock(value: datetime) -> datetime:
    return value.astimezone(KST).replace(tzinfo=None) if value.tzinfo else value


@dataclass(frozen=True)
class GapRange:
    event_type: str
    code: str
    market: str
    start: datetime
    end: datetime

    def document(self) -> dict[str, object]:
        return {"event_type": self.event_type, "code": self.code, "market": self.market,
                "start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class RealtimeGap:
    dropped_events: int
    ranges: tuple[GapRange, ...] = ()
    scope_unknown: bool = True

    def document(self) -> dict[str, object]:
        return {"type": "realtime_gap", "dropped_events": self.dropped_events,
                "ranges": [item.document() for item in self.ranges],
                "scope_unknown": self.scope_unknown}

    @classmethod
    def parse(cls, value: object) -> RealtimeGap | None:
        if type(value) is int:
            return cls(value) if value > 0 else None
        if not isinstance(value, dict) or type(value.get("dropped_events")) is not int:
            return None
        count = value["dropped_events"]
        if count <= 0:
            return None
        rows = value.get("ranges")
        if not isinstance(rows, list) or len(rows) > 400:
            return cls(count)
        ranges = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid gap range")
                kind = str(row["event_type"])
                code, market = str(row.get("code", "")), str(row.get("market", ""))
                start = market_clock(datetime.fromisoformat(str(row["start"])))
                end = market_clock(datetime.fromisoformat(str(row["end"])))
                if end < start or end - start > timedelta(days=1):
                    raise ValueError("unbounded gap range")
                if kind not in {"trade", "market_state", "program_trade", "stock_reference"}:
                    raise ValueError("unknown event type")
                if kind in {"trade", "program_trade", "stock_reference"} and not code:
                    raise ValueError("missing gap code")
                ranges.append(GapRange(kind, code, market, start, end))
        except (KeyError, TypeError, ValueError, OverflowError):
            return cls(count)
        return cls(count, tuple(ranges), value.get("scope_unknown") is not False)
