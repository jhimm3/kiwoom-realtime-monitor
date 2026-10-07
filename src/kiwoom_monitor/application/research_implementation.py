"""Canonical scientific implementation hash shared by PC research and NAS admission."""

from __future__ import annotations

import hashlib
from pathlib import Path

from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)


LEGACY_RESEARCH_IMPLEMENTATION_HASH = "a7f4d77894d29723bad8b3d7a22ad4c0d35b88c2f9e7f182c81a606914e8db5f"


def research_implementation_hash(session_profile: str | None = None) -> str:
    if session_profile is None:
        return LEGACY_RESEARCH_IMPLEMENTATION_HASH
    if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    root = Path(__file__).resolve().parents[3]
    paths = (
        root / "src/kiwoom_monitor/application/market_session_schedule.py",
        root / "src/kiwoom_monitor/application/research_factors.py",
        root / "src/kiwoom_monitor/application/research_replay.py",
        root / "src/kiwoom_monitor/application/market_research_features.py",
        root / "src/kiwoom_monitor/application/breakout_strategy.py",
        root / "src/kiwoom_monitor/application/pullback_reacceleration_strategy.py",
        root / "src/kiwoom_monitor/application/research_families.py",
        root / "src/kiwoom_monitor/infrastructure/persistence/research_repository.py",
        root / "src/kiwoom_monitor/infrastructure/research_data_source.py",
        root / "src/kiwoom_monitor/application/research_execution.py",
        root / "src/kiwoom_monitor/application/research_evaluation.py",
        root / "src/kiwoom_monitor/application/research_splits.py",
        root / "src/kiwoom_monitor/domain/ranking.py",
        root / "scripts/run_research.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
