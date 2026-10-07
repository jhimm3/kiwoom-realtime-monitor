"""아이콘 미리보기의 바깥 배경을 투명 처리하고 Windows ICO를 만든다."""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image


def is_neutral_background(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, _alpha = pixel
    return max(red, green, blue) - min(red, green, blue) < 12


def remove_edge_background(image: Image.Image) -> Image.Image:
    result = image.convert("RGBA")
    pixels = result.load()
    width, height = result.size
    pending = deque(
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(height)]
        + [(width - 1, y) for y in range(height)]
    )
    removed: set[tuple[int, int]] = set()

    while pending:
        x, y = pending.popleft()
        if (x, y) in removed or not (0 <= x < width and 0 <= y < height):
            continue
        if not is_neutral_background(pixels[x, y]):
            continue
        removed.add((x, y))
        pending.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    for position in removed:
        pixels[position] = (0, 0, 0, 0)
    return result


def main() -> None:
    source, png_path, ico_path = map(Path, sys.argv[1:4])
    icon = remove_edge_background(Image.open(source))
    icon.save(png_path)
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
