from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ThemeSuggestion:
    stock_code: str
    news_identity: str
    raw_theme_name: str
    evidence: str
    confidence: int
    provider: str
    model: str
    body_hash: str
    analyzed_at: datetime


@dataclass(frozen=True)
class ProfileThemeSuggestion:
    stock_code: str
    stock_name: str
    news_identity: str
    raw_theme_name: str
    resolved_theme_names: tuple[str, ...]
    evidence: str
    confidence: int
    status: str
    provider: str
    model: str
    analyzed_at: datetime

    @property
    def key(self) -> tuple[str, str, str]:
        return self.stock_code, self.news_identity, self.raw_theme_name
