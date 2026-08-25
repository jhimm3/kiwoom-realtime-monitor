from __future__ import annotations

from colorsys import hls_to_rgb, rgb_to_hls


def text_color(background: str) -> str:
    """Return a readable foreground in the same hue family as the badge."""
    value = background.lstrip("#")
    if len(value) != 6:
        return "#222222"
    try:
        red, green, blue = (int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError:
        return "#222222"
    hue, lightness, saturation = rgb_to_hls(red, green, blue)
    if saturation < 0.08:
        return "#333333" if lightness > 0.5 else "#EEEEEE"
    # 연한 배지는 같은 색조의 짙은 글자, 짙은 배지는 같은 색조의 밝은 글자.
    target_lightness = 0.27 if lightness >= 0.5 else 0.88
    target_saturation = max(0.42, min(0.92, saturation + 0.15))
    red, green, blue = hls_to_rgb(hue, target_lightness, target_saturation)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"
