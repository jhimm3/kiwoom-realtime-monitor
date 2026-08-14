from __future__ import annotations

def strength_badge(value: float | None, interest: float = 0.5, caution: float = 1.0, fire: float = 2.0, show_icon: bool = True, decimals: int = 2, icons: tuple[str, str, str] = ("👀", "⚠️", "🔥")) -> str:
    if value is None: return "-"
    interest_icon, caution_icon, fire_icon = icons
    icon = fire_icon if value >= fire else caution_icon if value >= caution else interest_icon if value >= interest else ""
    return f"{value:.{decimals}f}%" + (f" {icon}" if show_icon and icon else "")
