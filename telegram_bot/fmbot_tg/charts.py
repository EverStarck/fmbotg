"""Album collage rendering (Pillow replacement for FMBot.Images' SkiaSharp chart code)."""

import io
import re
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

TILE = 300
MAX_SIDE = 10
_SIZE_RE = re.compile(r"^(\d{1,2})x(\d{1,2})$", re.I)


def parse_size(token: str) -> tuple[int, int] | None:
    """'4x3' -> (4 columns, 3 rows). None if it isn't a size or is out of range."""
    match = _SIZE_RE.match(token)
    if not match:
        return None
    cols, rows = int(match[1]), int(match[2])
    if not (1 <= cols <= MAX_SIDE and 1 <= rows <= MAX_SIDE):
        return None
    return cols, rows


@dataclass
class Tile:
    image: bytes | None
    title: str
    subtitle: str


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render(tiles: list[Tile], cols: int, rows: int, titles: bool = True) -> bytes:
    canvas = Image.new("RGB", (cols * TILE, rows * TILE), (24, 24, 24))
    draw = ImageDraw.Draw(canvas, "RGBA")
    title_font, sub_font = _load_font(20), _load_font(16)

    for index, tile in enumerate(tiles[: cols * rows]):
        x, y = (index % cols) * TILE, (index // cols) * TILE
        cover = None
        if tile.image:
            try:
                cover = Image.open(io.BytesIO(tile.image)).convert("RGB").resize((TILE, TILE))
            except Exception:
                cover = None
        if cover is not None:
            canvas.paste(cover, (x, y))
        else:
            draw.rectangle((x, y, x + TILE - 1, y + TILE - 1), fill=(48, 48, 48))

        if titles or cover is None:
            pad = 8
            draw.rectangle((x, y, x + TILE, y + 50), fill=(0, 0, 0, 170))
            draw.text((x + pad, y + 4), _fit(draw, tile.subtitle, sub_font, TILE - 2 * pad), font=sub_font,
                      fill=(220, 220, 220))
            draw.text((x + pad, y + 24), _fit(draw, tile.title, title_font, TILE - 2 * pad), font=title_font,
                      fill=(255, 255, 255))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()
