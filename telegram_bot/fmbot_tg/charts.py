"""Album collage rendering (Pillow replacement for FMBot.Images' SkiaSharp chart code)."""

import io
import os
import re
from functools import lru_cache
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


# Pillow has no per-glyph fallback, so pick one font that covers as much music metadata as possible:
# Noto CJK covers Latin, Cyrillic, Greek and CJK. Pillow's bundled default has no accented letters.
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Debian fonts-noto-cjk (see Dockerfile)
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian fonts-dejavu-core
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialuni.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


@lru_cache(maxsize=None)
def _font_path() -> str | None:
    override = os.getenv("FONT_PATH")
    for path in ([override] if override else []) + FONT_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=32)
def _load_font(size: int) -> ImageFont.ImageFont:
    path = _font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
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


RECEIPT_WIDTH = 560
PAPER, INK, FAINT = (250, 248, 240), (30, 30, 30), (120, 120, 120)


def render_receipt(username: str, period_label: str, date_text: str, items: list[tuple[str, str, int]],
                   total_scrobbles: int) -> bytes:
    """Store-receipt style image of top tracks (port of FMBot's receipt command).

    `items` are (track, artist, plays), already in rank order.
    """
    pad, row = 32, 26
    big, mid, small = _load_font(30), _load_font(18), _load_font(15)
    height = 230 + row * 2 * len(items) + 170
    image = Image.new("RGB", (RECEIPT_WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    width = RECEIPT_WIDTH - 2 * pad

    def centered(y: int, text: str, font, fill=INK) -> None:
        text = _fit(draw, text, font, width)
        draw.text(((RECEIPT_WIDTH - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)

    def rule(y: int) -> None:
        x = pad
        while x < RECEIPT_WIDTH - pad:
            draw.line((x, y + 8, min(x + 6, RECEIPT_WIDTH - pad), y + 8), fill=FAINT, width=1)
            x += 11

    centered(pad, "FMBOT RECEIPT", big)
    centered(pad + 44, f"CUSTOMER: {username}", mid)
    centered(pad + 70, f"{period_label.upper()} · {date_text}", small, FAINT)
    y = pad + 104
    rule(y)
    y += 24
    draw.text((pad, y), "QTY  ITEM", font=small, fill=FAINT)
    draw.text((RECEIPT_WIDTH - pad - draw.textlength("PLAYS", font=small), y), "PLAYS", font=small, fill=FAINT)
    y += row

    for rank, (track, artist, plays) in enumerate(items, 1):
        count = str(plays)
        count_width = draw.textlength(count, font=mid)
        label = _fit(draw, f"{rank:>2}   {track}", mid, width - count_width - 16)
        draw.text((pad, y), label, font=mid, fill=INK)
        draw.text((RECEIPT_WIDTH - pad - count_width, y), count, font=mid, fill=INK)
        draw.text((pad + 34, y + row - 4), _fit(draw, artist, small, width - 34), font=small, fill=FAINT)
        y += row * 2

    rule(y)
    y += 28
    shown = sum(p for _, _, p in items)
    for label, value in (("ITEMS SHOWN", f"{shown:,}"), ("TOTAL SCROBBLES", f"{total_scrobbles:,}")):
        draw.text((pad, y), label, font=mid, fill=INK)
        draw.text((RECEIPT_WIDTH - pad - draw.textlength(value, font=mid), y), value, font=mid, fill=INK)
        y += row + 4
    y += 16
    centered(y, "THANK YOU FOR LISTENING", mid)
    centered(y + 28, "last.fm/user/" + username, small, FAINT)

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
