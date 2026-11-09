"""Portable KRX VI episodes for frozen MICRO research."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from kiwoom_monitor.domain.time_basis import aware_iso_kst


MICRO_HISTORICAL_VI_VERSION = "micro-historical-vi/v1"


@dataclass(frozen=True)
class HistoricalViEpisode:
    event_id: str
    triggered_at: datetime
    released_at: datetime | None
    trigger_price_won: int
    direction: str | None
    vi_type: str
    source_sha256: str

    @property
    def activation_ref(self) -> str:
        return f"krx-archive-vi:{self.event_id}:activation"

    def at_cut(self, cut: datetime) -> dict[str, object]:
        released = self.released_at is not None and self.released_at <= cut
        return {"event_id": self.event_id, "activation_ref": self.activation_ref,
                "triggered_at": self.triggered_at.isoformat(),
                "released_at": self.released_at.isoformat() if released else None,
                "trigger_price_won": self.trigger_price_won, "direction": self.direction,
                "vi_type": self.vi_type, "source_sha256": self.source_sha256}


@dataclass(frozen=True)
class HistoricalMicroVi:
    source_ref: str
    source_version: str
    trading_date: date
    archive_start: date
    archive_end: date
    episodes: tuple[HistoricalViEpisode, ...]

    @classmethod
    def from_document(cls, document: object, *, symbol: str, venue: str, cut: datetime) -> HistoricalMicroVi:
        if (not isinstance(document, Mapping) or document.get("version") != MICRO_HISTORICAL_VI_VERSION
                or venue != "KRX" or document.get("symbol") != symbol):
            raise ValueError("historical VI requires the same KRX research symbol")
        if any(not isinstance(document.get(key), str) or not document[key].strip()
               for key in ("source_ref", "source_version")):
            raise ValueError("historical VI requires its archive identity")
        try:
            day, start, end = (date.fromisoformat(document[key])
                               for key in ("trading_date", "archive_start", "archive_end"))
            rows = document["events"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid historical VI archive scope") from error
        if not isinstance(rows, list) or start > end or day > cut.date() or (rows and not start <= day <= end):
            raise ValueError("invalid historical VI archive scope")
        episodes: dict[str, HistoricalViEpisode] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("invalid historical VI event")
            try:
                trigger = aware_iso_kst(row["triggered_at"])
                release = aware_iso_kst(row["released_at"]) if row.get("released_at") is not None else None
                event = HistoricalViEpisode(row["event_id"], trigger, release, row["trigger_price_won"],
                                            row["direction"], row["vi_type"], row["source_sha256"])
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid historical VI event") from error
            if (event.triggered_at.date() != day or (release is not None and release < trigger)
                    or type(event.trigger_price_won) is not int or event.trigger_price_won <= 0
                    or event.direction not in ("UP", "DOWN", None)
                    or any(not isinstance(value, str) or not value for value in
                           (event.event_id, event.vi_type, event.source_sha256))):
                raise ValueError("invalid historical VI event")
            if episodes.setdefault(event.event_id, event) != event:
                raise ValueError("conflicting historical VI event")
        ordered = tuple(sorted(episodes.values(), key=lambda event: (event.triggered_at, event.event_id)))
        return cls(document["source_ref"], document["source_version"], day, start, end, ordered)

    def match_release(self, observed_at: datetime) -> HistoricalViEpisode | None:
        # KRX archive clocks have second precision; simultaneous rows are ambiguous.
        second = observed_at.replace(microsecond=0)
        matches = [episode for episode in self.episodes if episode.released_at == second]
        return matches[0] if len(matches) == 1 else None

    def at_cut(self, cut: datetime) -> dict[str, object]:
        visible = [episode.at_cut(cut) for episode in self.episodes if episode.triggered_at <= cut]
        in_range = self.archive_start <= self.trading_date <= self.archive_end
        return {"version": MICRO_HISTORICAL_VI_VERSION, "source_ref": self.source_ref,
                "source_version": self.source_version, "trading_date": self.trading_date.isoformat(),
                "archive_start": self.archive_start.isoformat(), "archive_end": self.archive_end.isoformat(),
                "availability_basis": "reconstructed_exchange_clock", "cut": cut.isoformat(),
                "coverage": "observed" if visible else "no_rows_at_cut" if in_range else "outside_archive",
                "episodes": visible}
