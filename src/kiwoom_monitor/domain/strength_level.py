from __future__ import annotations

def strength_badge(value: float | None, interest: float = 0.5, caution: float = 1.0, fire: float = 2.0, show_icon: bool = True) -> str:
    if value is None: return "-"
    icon = "🔥" if value >= fire else "⚠️" if value >= caution else "👀" if value >= interest else ""
    return f"{value:.2f}%" + (f" {icon}" if show_icon and icon else "")
