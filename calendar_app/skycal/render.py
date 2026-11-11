"""Draw one week as a 1280x800 landscape image. Pure function of its inputs; no I/O except fonts."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .feed import Event, on_day

W, H = 1280, 800
HEADER_H = 76
DAYHEAD_H = 84
PAD = 6

BG = (250, 248, 244)
INK = (28, 28, 30)
MUTED = (120, 120, 128)
RULE = (224, 220, 212)
TODAY_BG = (255, 255, 255)
ACCENT = (0, 110, 220)
WARN = (190, 70, 30)
CAL_COLORS = [(0, 110, 220), (30, 150, 90), (200, 90, 40), (140, 80, 190), (200, 50, 90), (90, 100, 120)]

# Hit boxes for navigation, shared with the touch handler: name -> (x0, y0, x1, y1).
NAV = {"prev": (1010, 12, 1074, 64), "today": (1082, 12, 1198, 64), "next": (1206, 12, 1270, 64)}

_FONT_DIRS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if (size, bold) not in _cache:
        for regular, heavy in _FONT_DIRS:
            if Path(regular).exists():
                _cache[(size, bold)] = ImageFont.truetype(heavy if bold else regular, size)
                break
        else:
            raise RuntimeError("no font found; install fonts-dejavu-core")
    return _cache[(size, bold)]


def week_start(day: date, first_weekday: int = 6) -> date:
    """First day of the week containing `day`. first_weekday: Monday=0 .. Sunday=6."""
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def fmt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "a" if dt.hour < 12 else "p"
    return f"{hour}{suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d}{suffix}"


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while draw.textlength(word, font=fnt) > width and len(word) > 1:  # hard-break a long word
            cut = len(word) - 1
            while cut > 1 and draw.textlength(word[:cut], font=fnt) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=fnt) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _header(d: ImageDraw.ImageDraw, start: date, note: str, stale: bool) -> None:
    end = start + timedelta(days=6)
    if start.month == end.month:
        title = f"{start:%B} {start.day} to {end.day}, {end.year}"
    elif start.year == end.year:
        title = f"{start:%b} {start.day} to {end:%b} {end.day}, {end.year}"
    else:
        title = f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"
    d.text((20, HEADER_H // 2), title, font=font(34, True), fill=INK, anchor="lm")
    d.text((NAV["prev"][0] - 20, HEADER_H // 2), note, font=font(17), fill=WARN if stale else MUTED, anchor="rm")
    for name, label in (("prev", "‹"), ("today", "Today"), ("next", "›")):
        x0, y0, x1, y1 = NAV[name]
        d.rounded_rectangle((x0, y0, x1, y1), radius=12, fill=TODAY_BG, outline=RULE, width=2)
        size = 22 if name == "today" else 36
        d.text(((x0 + x1) // 2, (y0 + y1) // 2 - (0 if name == "today" else 4)), label,
               font=font(size, True), fill=INK, anchor="mm")


def _day(d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, events: list[Event], today: date, tz: ZoneInfo) -> None:
    is_today = day == today
    if is_today:
        d.rectangle((x0, HEADER_H, x1, H), fill=TODAY_BG)
    cx = (x0 + x1) // 2
    d.text((cx, HEADER_H + 20), f"{day:%a}".upper(), font=font(17, True), fill=ACCENT if is_today else MUTED, anchor="mm")
    if is_today:
        d.ellipse((cx - 23, HEADER_H + 33, cx + 23, HEADER_H + 79), fill=ACCENT)
    d.text((cx, HEADER_H + 56), str(day.day), font=font(28, True),
           fill=(255, 255, 255) if is_today else (MUTED if day < today else INK), anchor="mm")

    y = HEADER_H + DAYHEAD_H + PAD
    left, right = x0 + PAD, x1 - PAD
    text_w = right - left - 16
    title_f, time_f = font(18, True), font(15)
    lo = datetime.combine(day, time.min, tz)
    for i, ev in enumerate(events):
        color = CAL_COLORS[ev.cal % len(CAL_COLORS)]
        lines = wrap(d, ev.title, title_f, text_w, 3)
        label = "" if ev.all_day else (fmt_time(ev.start) if ev.start >= lo else "continues")
        h = 10 + (19 if label else 0) + 22 * len(lines) + 6
        if y + h > H - 30 and i < len(events) - 1 or y + h > H - 4:
            d.text((cx, H - 16), f"+{len(events) - i} more", font=font(16, True), fill=MUTED, anchor="mm")
            break
        if ev.all_day:
            d.rounded_rectangle((left, y, right, y + h), radius=8, fill=color)
            ink = sub = (255, 255, 255)
        else:
            tint = tuple(int(c + (255 - c) * 0.86) for c in color)
            d.rounded_rectangle((left, y, right, y + h), radius=8, fill=tint)
            d.rounded_rectangle((left, y, left + 5, y + h), radius=2, fill=color)
            ink, sub = INK, color
        ty = y + 7
        if label:
            d.text((left + 12, ty), label, font=time_f, fill=sub)
            ty += 19
        for line in lines:
            d.text((left + 12, ty), line, font=title_f, fill=ink)
            ty += 22
        y += h + PAD


def render_week(start: date, events: list[Event], now: datetime, note: str | None = None, stale: bool = False) -> Image.Image:
    """`start` is the first day shown; `now` (tz-aware) sets today's highlight.
    `note` is the status text in the header; `stale` draws it as a warning."""
    tz = now.tzinfo
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _header(d, start, note if note is not None else f"Updated {fmt_time(now)}", stale)
    d.line((0, HEADER_H, W, HEADER_H), fill=RULE, width=2)
    edges = [round(i * W / 7) for i in range(8)]
    for i in range(7):
        day = start + timedelta(days=i)
        _day(d, edges[i], edges[i + 1], day, on_day(events, day, tz), now.date(), tz)
    for x in edges[1:-1]:
        d.line((x, HEADER_H, x, H), fill=RULE, width=1)
    d.line((0, HEADER_H + DAYHEAD_H, W, HEADER_H + DAYHEAD_H), fill=RULE, width=1)
    return img
