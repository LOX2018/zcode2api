"""Generate packaging/ZCodeHub.ico (exe / shortcut / tray mark).

Run by build.ps1 before PyInstaller. Deliberately independent of packaging/tray.py:
that module draws the same mark at runtime *with* a status dot, and importing it here
would drag pystray + the app package into a build-time step.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
OUT = Path(__file__).with_name("ZCodeHub.ico")


def draw_mark(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(1, size // 32)
    radius = max(2, size // 8)
    draw.rounded_rectangle(
        [pad, pad, size - 1 - pad, size - 1 - pad], radius=radius, fill=(30, 34, 46, 255)
    )
    text = "Z"
    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.58))
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (size - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (size - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(240, 242, 246, 255))
    return img


def main() -> None:
    mark = draw_mark(SIZE)
    mark.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
