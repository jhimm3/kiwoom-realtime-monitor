"""Canonical scientific implementation hash shared by PC research and NAS admission."""

from __future__ import annotations

from importlib.resources import files

from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)
from kiwoom_monitor.domain.research_contract import research_implementation_digest


LEGACY_RESEARCH_IMPLEMENTATION_HASH = "a7f4d77894d29723bad8b3d7a22ad4c0d35b88c2f9e7f182c81a606914e8db5f"


def research_implementation_hash(session_profile: str | None = None) -> str:
    if session_profile is None:
        return LEGACY_RESEARCH_IMPLEMENTATION_HASH
    if session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    package_root = files("kiwoom_monitor")
    return research_implementation_digest(
        lambda path: package_root.joinpath(*path.split("/")[2:]).read_bytes(),
    )
