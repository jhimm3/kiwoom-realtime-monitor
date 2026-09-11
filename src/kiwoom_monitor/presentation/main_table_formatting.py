from __future__ import annotations

from html import escape


def format_trade_value_eok(value: float, digits: int) -> str:
    """억 단위 값을 억 또는 조·억 표기로 변환한다."""
    if value < 10_000:
        return f"{value:,.{digits}f}억"
    jo = int(value // 10_000)
    remainder = value - jo * 10_000
    if remainder < 0.005:
        return f"{jo:,}조"
    formatted = f"{remainder:,.{digits}f}"
    if digits:
        whole, _, fraction = formatted.partition(".")
        formatted = whole if not fraction.rstrip("0") else f"{whole}.{fraction.rstrip('0')}"
    return f"{jo:,}조{formatted}억"


def trade_value_color(value: float) -> str:
    return "#C00000" if value >= 10_000 else "#0070C0"


def theme_trade_summary_html(top: list[tuple[str, float]], digits: int) -> str:
    return "&nbsp;&nbsp;".join(
        f"{index}. {escape(theme)} "
        f"<span style='background:{'#FCE4D6' if value >= 10_000 else '#EAF2F8'}; "
        f"border:1px solid {'#E6A57E' if value >= 10_000 else '#8FB9D9'}; "
        f"border-radius:4px; padding:2px 6px; color:{trade_value_color(value)}; "
        f"font-weight:700;'>{format_trade_value_eok(value, digits)}</span>"
        for index, (theme, value) in enumerate(top, start=1)
    )


def format_market_cap_eok(value: float) -> str:
    """시가총액의 조·억 경계를 눈에 띄게 구분한다."""
    text = format_trade_value_eok(value, 0)
    return text.replace("조", "조 · ", 1) if "조" in text and text.endswith("억") else text


def row_background_color(
    *,
    near_high: bool,
    selected: bool,
    rank_changed: bool,
    rank_changed_enabled: bool,
    rank_changed_color: str,
    rank: int,
    odd_color: str,
    even_color: str,
) -> str:
    """메인 표 행 배경의 기존 우선순위를 색상 문자열로 반환한다."""
    if near_high:
        return "#FDE9E7"
    if selected:
        return "#DDEBF7"
    if rank_changed and rank_changed_enabled:
        return rank_changed_color
    return odd_color if rank % 2 else even_color


def rank_highlight_duration_ms(value: object) -> int:
    """순위 변경 강조 초 설정을 밀리초로 해석한다."""
    try:
        return round(float(value) * 1_000)
    except (TypeError, ValueError):
        return 2_000


def decimal_places(value: object, column: str) -> int:
    """표시 소수 자릿수를 열별 기존 허용 범위로 제한한다."""
    try:
        return max(0, min(8 if column == "strength" else 4, int(value)))
    except (TypeError, ValueError):
        return 2


def change_rate_text_color(value: object) -> str | None:
    """등락률의 부호에 맞는 글자색을 반환한다."""
    try:
        rate = float(str(value).strip().replace("%", "").replace(",", ""))
    except ValueError:
        return None
    if rate > 0:
        return "#C00000"
    if rate < 0:
        return "#0070C0"
    return None
