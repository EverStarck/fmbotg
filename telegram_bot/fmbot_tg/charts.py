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


# --- iceberg (port of PuppeteerService.CreatePopularityIcebergImage) ------------------------------

ICEBERG_WIDTH, ICEBERG_HEIGHT = 1000, 1400
WATERLINE = 360


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def render_iceberg(username: str, period_label: str, sections: list[tuple[str, list[str]]]) -> bytes:
    """Ten depth bands, best known at the tip. `sections` is (label, artist names) from the top down."""
    width, height = ICEBERG_WIDTH, ICEBERG_HEIGHT
    canvas = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Sky above the waterline, then water getting darker with depth
    for y in range(height):
        if y < WATERLINE:
            color = _mix((196, 228, 247), (230, 244, 252), y / WATERLINE)
        else:
            color = _mix((38, 120, 178), (0, 18, 44), (y - WATERLINE) / (height - WATERLINE))
        draw.line((0, y, width, y), fill=color)

    # The berg: a narrow tip above water, widening below. Above water it's white; below, a lighter
    # version of the water around it, so it fades with depth like a real one.
    center = width // 2
    outline = [(center - 30, 135), (center + 60, 190), (center + 165, WATERLINE), (center + 390, 640),
               (center + 330, 1050), (center + 140, height - 40), (center - 170, height - 70),
               (center - 380, 1000), (center - 410, 620), (center - 175, WATERLINE), (center - 95, 230)]
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).polygon(outline, fill=255)
    berg = Image.blend(canvas, Image.new("RGB", (width, height), (255, 255, 255)), 0.32)
    berg.paste((240, 248, 253), (0, 0, width, WATERLINE))
    canvas.paste(berg, (0, 0), mask)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.line((0, WATERLINE, width, WATERLINE), fill=(255, 255, 255, 180), width=3)

    title_font, label_font, name_font = _load_font(54), _load_font(22), _load_font(30)
    draw.text((40, 30), _fit(draw, f"{username}'s iceberg", title_font, width - 80), font=title_font, fill=(20, 30, 45))
    draw.text((40, 98), period_label, font=label_font, fill=(60, 80, 100))

    top, bottom = 170, height - 50
    band = (bottom - top) / max(1, len(sections))
    for index, (label, names) in enumerate(sections):
        y0 = top + index * band
        dark = y0 + band / 2 > WATERLINE
        text_color = (255, 255, 255) if dark else (20, 30, 45)
        stroke = (0, 27, 58) if dark else (255, 255, 255)
        draw.text((24, y0 + 6), label, font=label_font, fill=(200, 225, 245) if dark else (60, 80, 100))
        # Up to two lines of names, centered in the band
        lines, current = [], ""
        for name in names:
            candidate = f"{current}  ·  {name}" if current else name
            if draw.textlength(candidate, font=name_font) > width - 120 and current:
                lines.append(current)
                current = name
            else:
                current = candidate
        if current:
            lines.append(current)
        lines = [_fit(draw, line, name_font, width - 120) for line in lines[:2]]
        line_height = 38
        y = y0 + (band - line_height * len(lines)) / 2 + 8
        for line in lines:
            draw.text((width / 2, y), line, font=name_font, fill=text_color, anchor="ma",
                      stroke_width=2 if dark else 0, stroke_fill=stroke)
            y += line_height

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()
