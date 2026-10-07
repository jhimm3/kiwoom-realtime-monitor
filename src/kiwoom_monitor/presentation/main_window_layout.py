"""메인 창 표 배치에 사용하는 부작용 없는 크기 계산."""

from __future__ import annotations


def clamp_uniform_row_height(value: int) -> int:
    """사용자가 드래그한 행 높이를 기존 허용 범위로 제한한다."""
    return max(12, min(100, int(value)))


def responsive_row_height(
    row_count: int,
    viewport_height: int,
    fallback_height: int,
) -> int:
    """현재 표 높이에 맞춘 반응형 행 높이를 반환한다."""
    count = max(1, int(row_count))
    fitted = int(viewport_height) // count if viewport_height > 0 else 0
    return max(14, min(64, fitted or int(fallback_height)))


def proportional_column_widths(
    current_widths: tuple[int, ...],
    available_width: int,
) -> tuple[int, ...]:
    """보이는 열의 현재 비율을 유지해 새 너비를 계산한다."""
    if not current_widths:
        return ()
    current_total = sum(current_widths)
    if available_width <= 0 or current_total <= 0 or available_width == current_total:
        return current_widths
    widths = [max(40, round(width * available_width / current_total)) for width in current_widths]
    difference = available_width - sum(widths)
    widths[-1] = max(40, widths[-1] + difference)
    return tuple(widths)


def fitted_window_size(
    *,
    window_width: int,
    window_height: int,
    minimum_width: int,
    minimum_height: int,
    table_width: int,
    table_height: int,
    content_width: int,
    content_height: int,
) -> tuple[int, int]:
    """표 내용과 현재 창·표 크기의 차이로 맞춤 창 크기를 계산한다."""
    target_width = window_width + content_width - table_width
    target_height = window_height + content_height - table_height
    return max(minimum_width, target_width), max(minimum_height, target_height)


def parse_window_position(x_value: object, y_value: object) -> tuple[int, int] | None:
    """설정 저장소의 좌표를 안전하게 해석한다."""
    try:
        return int(x_value), int(y_value)
    except (TypeError, ValueError):
        return None
